"""
GoalManager + AutonomyMatrix — autonomous goal routing.

Goals are routed to one of three tiers:
  AUTONOMOUS  — act immediately, log after (high confidence, low impact, low cost)
  ASSISTED    — notify user, proceed unless overridden within timeout
  ADVISORY    — enqueue for explicit human approval via DecisionQueue

Thresholds are configurable via <workspace>/config/guardrails.yaml.
"""
from __future__ import annotations

import asyncio
import dataclasses
import enum
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.autonomy.goal_manager")


class AutonomyLevel(enum.Enum):
    AUTONOMOUS = "autonomous"   # act immediately
    ASSISTED   = "assisted"     # notify, proceed unless overridden
    ADVISORY   = "advisory"     # queue for explicit approval


@dataclasses.dataclass
class Goal:
    """A goal proposed by the CuriosityEngine or an external trigger."""
    id:             str
    skill:          str
    params:         dict
    confidence:     float          = 0.5
    impact:         str            = "low"    # "low" | "medium" | "high"
    estimated_cost: float          = 0.0      # estimated USD cost
    source:         str            = "system" # trigger source label
    created_at:     float          = dataclasses.field(default_factory=time.time)
    priority:       int            = 5        # 1 = highest

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class AutonomyThresholds:
    """Configurable thresholds read from guardrails.yaml."""
    autonomous_confidence:  float = 0.9
    autonomous_cost:        float = 0.01
    assisted_confidence:    float = 0.7
    assisted_cost:          float = 0.10
    assisted_timeout_s:     float = 30.0   # seconds to wait before proceeding


class AutonomyMatrix:
    """
    Maps (confidence, impact, estimated_cost) → AutonomyLevel.
    Thresholds are read from <workspace>/config/guardrails.yaml.
    """

    def __init__(self, workspace: Path | None = None) -> None:
        self._thresholds = self._load_thresholds(workspace)

    def _load_thresholds(self, workspace: Path | None) -> AutonomyThresholds:
        t = AutonomyThresholds()
        if workspace is None:
            return t
        yaml_path = workspace / "config" / "guardrails.yaml"
        if not yaml_path.exists():
            return t
        try:
            import yaml  # type: ignore
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            atm = raw.get("autonomy_matrix", {})
            if "autonomous_confidence" in atm:
                t.autonomous_confidence = float(atm["autonomous_confidence"])
            if "autonomous_cost" in atm:
                t.autonomous_cost = float(atm["autonomous_cost"])
            if "assisted_confidence" in atm:
                t.assisted_confidence = float(atm["assisted_confidence"])
            if "assisted_cost" in atm:
                t.assisted_cost = float(atm["assisted_cost"])
            if "assisted_timeout_s" in atm:
                t.assisted_timeout_s = float(atm["assisted_timeout_s"])
        except Exception as exc:
            log.warning("autonomy_thresholds_load_error",
                        extra={"error": str(exc)[:120]})
        return t

    def evaluate(self, goal: Goal) -> AutonomyLevel:
        t = self._thresholds
        if (goal.confidence >= t.autonomous_confidence
                and goal.impact == "low"
                and goal.estimated_cost < t.autonomous_cost):
            return AutonomyLevel.AUTONOMOUS
        if (goal.confidence >= t.assisted_confidence
                and goal.impact in ("low", "medium")
                and goal.estimated_cost < t.assisted_cost):
            return AutonomyLevel.ASSISTED
        return AutonomyLevel.ADVISORY


class GoalManager:
    """
    Receives Goal objects from CuriosityEngine, evaluates autonomy tier,
    and routes to immediate execution, notification-with-timeout,
    or DecisionQueue as appropriate.

    Wire into boot_kernel() and call drain() from Kernel.tick().
    """

    def __init__(self,
                 kernel: Any = None,
                 decision_queue: Any = None,
                 audit_logger: Any = None,
                 workspace: Path | None = None) -> None:
        self._kernel         = kernel
        self._decision_queue = decision_queue
        self._audit_logger   = audit_logger
        self._matrix         = AutonomyMatrix(workspace)
        self._pending_goals: list[Goal] = []
        self._autonomous_queue: list[Goal] = []

    async def submit(self, goal: Goal) -> AutonomyLevel:
        """Evaluate and route a goal. Returns the autonomy level used."""
        level = self._matrix.evaluate(goal)

        try:
            match level:
                case AutonomyLevel.AUTONOMOUS:
                    self._autonomous_queue.append(goal)
                    log.info("goal_autonomous_queued",
                             extra={"goal_id": goal.id, "skill": goal.skill})
                case AutonomyLevel.ASSISTED:
                    await self._notify_and_schedule(goal)
                case AutonomyLevel.ADVISORY:
                    await self._enqueue_for_approval(goal)
        except Exception as exc:
            log.warning("goal_routing_error",
                        extra={"goal_id": goal.id, "error": str(exc)[:120]})

        if self._audit_logger is not None:
            try:
                self._audit_logger.log(
                    event_type="autonomous_action",
                    actor="system",
                    action=goal.skill,
                    resource=str(goal.params),
                    outcome=level.value,
                )
            except Exception:
                pass

        return level

    async def _notify_and_schedule(self, goal: Goal) -> None:
        """Notify and schedule with timeout — proceed unless overridden."""
        log.info("goal_assisted_notified",
                 extra={"goal_id": goal.id, "skill": goal.skill})
        self._pending_goals.append(goal)
        # In a real deployment, a notification would be sent to the UI here.
        # The goal proceeds after assisted_timeout_s unless cancelled.
        timeout = self._matrix._thresholds.assisted_timeout_s
        await asyncio.sleep(min(timeout, 5.0))  # capped for responsiveness
        if goal in self._pending_goals:
            self._pending_goals.remove(goal)
            self._autonomous_queue.append(goal)
            log.info("goal_assisted_timeout_proceed",
                     extra={"goal_id": goal.id})

    async def _enqueue_for_approval(self, goal: Goal) -> None:
        """Enqueue goal in DecisionQueue for explicit human approval."""
        if self._decision_queue is not None:
            try:
                from essence.agents.decision import Decision, DecisionPriority
                import uuid
                decision = Decision(
                    decision_id=str(uuid.uuid4()),
                    tool_name=goal.skill,
                    args=goal.params,
                    priority=DecisionPriority.MEDIUM,
                    reason=f"Autonomous goal: {goal.skill} (confidence={goal.confidence:.2f})",
                    created_at=time.time(),
                    expires_at=time.time() + 3600,
                    session_id="system",
                )
                await asyncio.get_event_loop().run_in_executor(
                    None, self._decision_queue.enqueue, decision)
                log.info("goal_advisory_queued",
                         extra={"goal_id": goal.id, "decision_id": decision.decision_id})
            except Exception as exc:
                log.warning("goal_decision_enqueue_error",
                            extra={"error": str(exc)[:120]})
        else:
            self._pending_goals.append(goal)
            log.info("goal_advisory_pending_no_queue",
                     extra={"goal_id": goal.id})

    def drain_autonomous(self) -> list[Goal]:
        """
        Return and clear all AUTONOMOUS-tier goals from the queue.
        Called by Kernel.tick() on each iteration.
        """
        if not self._autonomous_queue:
            return []
        goals = list(self._autonomous_queue)
        self._autonomous_queue.clear()
        return goals

    def cancel_goal(self, goal_id: str) -> bool:
        """Cancel a pending ASSISTED-tier goal before it proceeds."""
        for i, g in enumerate(self._pending_goals):
            if g.id == goal_id:
                self._pending_goals.pop(i)
                log.info("goal_cancelled", extra={"goal_id": goal_id})
                return True
        return False

    def list_pending(self) -> list[dict]:
        return [g.to_dict() for g in self._pending_goals]

    def stats(self) -> dict:
        return {
            "autonomous_queued": len(self._autonomous_queue),
            "pending":           len(self._pending_goals),
        }
