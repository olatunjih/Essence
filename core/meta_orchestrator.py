"""MetaOrchestrator — unified prefrontal cortex for the ESSENCE kernel.

Responsibilities:
  - Pre-request enrichment: inject twin, intent trajectory, and temporal
    goals into every LLM call.
  - Pre-execution gate chain: DryRunSimulator → WisdomEngine →
    TemporalPlane → TrustLedger in sequence.
  - Post-execution learning: route outcomes to MetaReflectionEngine,
    TrustLedger, and OpportunityEngine.
  - Conflict arbitration: when WisdomEngine objects but TrustLedger
    says "auto", escalate to HITL queue.
  - Periodic coordination: on heartbeat, trigger ResearchEngine,
    CognitiveHealthMonitor, and MemoryLifecycleManager.

The MetaOrchestrator is intentionally thin — it delegates to individual
engines. It is the routing spine, not the logic.
"""
from __future__ import annotations
import logging
from typing import Any

log = logging.getLogger("essence.core.meta_orchestrator")

_MEMORY_LIFECYCLE_INTERVAL_S = 86400   # run at most once per 24 h


class MetaOrchestrator:
    def __init__(self, subsystems: Any) -> None:
        self._s = subsystems
        self._last_lifecycle_run: float = 0.0

    def enrich_prompt_context(self, raw_query: str) -> str:
        """Build contextual prefix from twin, intent trajectory, temporal goals,
        and learned user preferences."""
        parts: list[str] = []
        s = self._s
        if getattr(s, "twin", None):
            parts.append(s.twin.context_block())
        if getattr(s, "intent_evolution", None):
            ev = s.intent_evolution.summary()
            parts.append(
                f"[INTENT TRAJECTORY] current={ev['current']} "
                f"future={ev['future']}")
        if getattr(s, "temporal", None):
            goals = s.temporal.all_goals()
            if goals:
                hs = " | ".join(
                    f"{g['horizon']}:{g['description'][:40]}"
                    for g in goals[:3])
                parts.append(f"[TEMPORAL GOALS] {hs}")
        if getattr(s, "user_preference_engine", None):
            try:
                hint = s.user_preference_engine.system_hint()
                if hint:
                    parts.append(hint)
            except Exception:
                pass
        return "\n".join(parts)

    def pre_execution_gate(self, plan_summary: str,
                           task_descriptions: list[str],
                           action_class: str = "general") -> dict:
        """Run DryRunSimulator → WisdomEngine → TemporalPlane → TrustLedger.
        Returns {"proceed": bool, "reason": str, "escalate": bool}
        """
        s = self._s

        if getattr(s, "simulator", None):
            sim = s.simulator.simulate(None, task_descriptions)
            if not sim.safe_to_proceed:
                return {"proceed": False,
                        "reason": sim.advice, "escalate": True}

        if getattr(s, "wisdom", None):
            verdict = s.wisdom.evaluate(plan_summary)
            if verdict.level == "OBJECT":
                return {"proceed": False,
                        "reason": verdict.advice, "escalate": True}

        if getattr(s, "temporal", None):
            conflict = s.temporal.check_conflict(plan_summary)
            if conflict.has_conflict:
                return {"proceed": True,
                        "reason": conflict.advice, "escalate": False}

        if getattr(s, "trust_ledger", None):
            level = s.trust_ledger.autonomy_for(action_class)
            if level == "require_approval":
                return {"proceed": False,
                        "reason": "Trust ledger requires explicit approval",
                        "escalate": True}

        return {"proceed": True,
                "reason": "All pre-execution gates passed.",
                "escalate": False}

    def post_execution_learn(self, episode: dict, accepted: bool) -> None:
        """Route outcomes to TrustLedger, MetaReflectionEngine, OpportunityEngine."""
        s = self._s
        action_class = episode.get("skill", "general")
        if getattr(s, "trust_ledger", None):
            s.trust_ledger.record(action_class, accepted)
        if accepted and getattr(s, "meta_reflector", None):
            s.meta_reflector.reflect(episode)
        if getattr(s, "opportunity_engine", None):
            s.opportunity_engine.observe(action_class)

        if getattr(s, "cognitive_health", None):
            try:
                score = episode.get("score", -1.0)
                if score >= 0:
                    s.cognitive_health.record_planning_score(float(score))
            except Exception as exc:
                log.debug("cognitive_health_score_record_error: %s", exc)

    def on_heartbeat(self) -> None:
        """Periodic coordination — runs on every heartbeat tick."""
        import time
        s = self._s

        try:
            if getattr(s, "research_engine", None):
                digests = s.research_engine.run_cycle()
                log.debug("research_cycle: %d digests", len(digests))
        except Exception as exc:
            log.warning("MetaOrchestrator heartbeat research error: %s", exc)

        try:
            if getattr(s, "cognitive_health", None):
                report = s.cognitive_health.assess()
                if getattr(s, "event_bus", None):
                    try:
                        s.event_bus.publish_sync("cognitive.health", report.to_dict())
                    except Exception:
                        pass
                if report.alerts:
                    log.warning("cognitive_health_alerts: %s", report.alerts)
                else:
                    log.debug("cognitive_health_ok: drift=%.2f goal_drift=%.2f",
                              report.memory_drift_score, report.goal_drift_score)
        except Exception as exc:
            log.warning("MetaOrchestrator heartbeat cognitive_health error: %s", exc)

        try:
            now = time.time()
            if (getattr(s, "memory_lifecycle", None)
                    and now - self._last_lifecycle_run >= _MEMORY_LIFECYCLE_INTERVAL_S):
                results = s.memory_lifecycle.run_cycle()
                self._last_lifecycle_run = now
                log.debug("memory_lifecycle_cycle: %s", results)
        except Exception as exc:
            log.warning("MetaOrchestrator heartbeat memory_lifecycle error: %s", exc)

        # ── Wave-2 module heartbeat hooks ─────────────────────────────────────

        # ReminderEngine.tick() — fire any reminders whose due time has passed
        try:
            if getattr(s, "reminder_engine", None):
                fired = s.reminder_engine.tick()
                if fired:
                    log.debug("reminder_engine_tick: fired=%d", len(fired))
        except Exception as exc:
            log.debug("reminder_engine_tick_error: %s", exc)

        # KnowledgeGapDetector.scan() — surface the next gap question if idle
        try:
            if getattr(s, "knowledge_gap_detector", None):
                gap = s.knowledge_gap_detector.scan()
                if gap and getattr(s, "event_bus", None):
                    try:
                        s.event_bus.publish_sync("knowledge.gap", gap)
                    except Exception:
                        pass
        except Exception as exc:
            log.debug("knowledge_gap_scan_error: %s", exc)

        # LearningCurveTracker.check_milestones() — emit milestone events
        try:
            if getattr(s, "learning_curve", None):
                milestones = s.learning_curve.check_milestones()
                if milestones and getattr(s, "event_bus", None):
                    for m in milestones:
                        try:
                            s.event_bus.publish_sync("learning.milestone", m)
                        except Exception:
                            pass
        except Exception as exc:
            log.debug("learning_curve_milestone_error: %s", exc)

        # SkillSystem — periodic discovery scan + flush dirty specs
        try:
            if getattr(s, "skill_system", None):
                s.skill_system.discovery.scan_local()
                s.skill_system.repository.flush()
        except Exception as exc:
            log.debug("skill_system_heartbeat_error: %s", exc)
