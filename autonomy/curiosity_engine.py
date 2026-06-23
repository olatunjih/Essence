"""
CuriosityEngine — proactive goal generation from typed triggers.

Trigger types:
  AnomalyTrigger   — statistical anomaly in recent observations
  DriftTrigger     — model or data distribution drift detected
  NoveltyTrigger   — novel entity or pattern observed
  ScheduleTrigger  — calendar-based periodic trigger

Wire into HeartbeatScheduler._tick() to call run_cycle() on each interval.
"""
from __future__ import annotations

import dataclasses
import logging
import time
import uuid
from typing import Any

log = logging.getLogger("essence.autonomy.curiosity_engine")


# ── Trigger base ──────────────────────────────────────────────────────────────

class Trigger:
    """Base class for all curiosity triggers."""

    def should_fire(self) -> bool:
        raise NotImplementedError

    def generate_goal(self) -> Any:
        raise NotImplementedError


# ── Concrete triggers ─────────────────────────────────────────────────────────

@dataclasses.dataclass
class AnomalyTrigger(Trigger):
    """Fires when a metric exceeds a z-score threshold."""
    name:          str
    metric_value:  float
    baseline_mean: float
    baseline_std:  float
    z_threshold:   float = 2.5
    skill:         str   = "anomaly_investigation"

    def should_fire(self) -> bool:
        if self.baseline_std == 0:
            return False
        z = abs(self.metric_value - self.baseline_mean) / self.baseline_std
        return z >= self.z_threshold

    def generate_goal(self) -> Any:
        from essence.autonomy.goal_manager import Goal
        return Goal(
            id=str(uuid.uuid4()),
            skill=self.skill,
            params={
                "metric":        self.name,
                "value":         self.metric_value,
                "baseline_mean": self.baseline_mean,
                "z_score":       abs(self.metric_value - self.baseline_mean) / max(self.baseline_std, 1e-9),
            },
            confidence=0.85,
            impact="low",
            estimated_cost=0.001,
            source="anomaly_trigger",
        )


@dataclasses.dataclass
class DriftTrigger(Trigger):
    """Fires when model or data drift is detected."""
    name:          str
    drift_score:   float
    drift_threshold: float = 0.15
    skill:         str   = "drift_investigation"

    def should_fire(self) -> bool:
        return self.drift_score >= self.drift_threshold

    def generate_goal(self) -> Any:
        from essence.autonomy.goal_manager import Goal
        return Goal(
            id=str(uuid.uuid4()),
            skill=self.skill,
            params={
                "drift_source": self.name,
                "drift_score":  self.drift_score,
            },
            confidence=0.75,
            impact="medium",
            estimated_cost=0.005,
            source="drift_trigger",
        )


@dataclasses.dataclass
class NoveltyTrigger(Trigger):
    """Fires when a novel entity or pattern is observed."""
    name:          str
    novelty_score: float
    novelty_threshold: float = 0.7
    skill:         str   = "novelty_exploration"

    def should_fire(self) -> bool:
        return self.novelty_score >= self.novelty_threshold

    def generate_goal(self) -> Any:
        from essence.autonomy.goal_manager import Goal
        return Goal(
            id=str(uuid.uuid4()),
            skill=self.skill,
            params={
                "entity":         self.name,
                "novelty_score":  self.novelty_score,
            },
            confidence=0.65,
            impact="low",
            estimated_cost=0.002,
            source="novelty_trigger",
        )


@dataclasses.dataclass
class ScheduleTrigger(Trigger):
    """Fires at regular intervals (calendar-based)."""
    name:         str
    interval_s:   float        # seconds between firings
    skill:        str
    params:       dict = dataclasses.field(default_factory=dict)
    _last_fired:  float = dataclasses.field(default=0.0, init=False)

    def should_fire(self) -> bool:
        return (time.time() - self._last_fired) >= self.interval_s

    def generate_goal(self) -> Any:
        from essence.autonomy.goal_manager import Goal
        self._last_fired = time.time()
        return Goal(
            id=str(uuid.uuid4()),
            skill=self.skill,
            params=dict(self.params),
            confidence=0.95,
            impact="low",
            estimated_cost=0.001,
            source=f"schedule:{self.name}",
        )


# ── CuriosityEngine ───────────────────────────────────────────────────────────

class CuriosityEngine:
    """
    Evaluates registered triggers and submits fired goals to GoalManager.

    Called by HeartbeatScheduler._tick() on each scheduler interval.
    """

    def __init__(self, goal_manager: Any = None) -> None:
        self._goal_manager: Any = goal_manager
        self._triggers: list[Trigger] = []

    def register(self, trigger: Trigger) -> None:
        """Register a trigger for evaluation on each cycle."""
        self._triggers.append(trigger)
        log.debug("curiosity_trigger_registered",
                  extra={"type": type(trigger).__name__})

    def remove(self, trigger: Trigger) -> bool:
        """Remove a previously registered trigger."""
        try:
            self._triggers.remove(trigger)
            return True
        except ValueError:
            return False

    async def run_cycle(self) -> int:
        """
        Evaluate all triggers and submit fired goals to GoalManager.
        Returns the number of goals submitted.
        """
        if not self._triggers:
            return 0

        submitted = 0
        for trigger in self._triggers:
            try:
                if trigger.should_fire():
                    goal = trigger.generate_goal()
                    if self._goal_manager is not None:
                        await self._goal_manager.submit(goal)
                        submitted += 1
                        log.info("curiosity_goal_submitted",
                                 extra={
                                     "trigger": type(trigger).__name__,
                                     "goal_id": goal.id,
                                     "skill":   goal.skill,
                                 })
            except Exception as exc:
                log.warning("curiosity_trigger_error",
                            extra={"trigger": type(trigger).__name__,
                                   "error": str(exc)[:120]})
        return submitted

    def list_triggers(self) -> list[dict]:
        return [
            {"type": type(t).__name__, "name": getattr(t, "name", "")}
            for t in self._triggers
        ]
