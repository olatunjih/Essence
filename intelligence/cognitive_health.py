"""CognitiveHealthMonitor — continuous assessment of cognitive integrity.

Checks:
  CH1 — Memory drift   : PersonalTwin entries not updated in > 60d
  CH2 — Goal drift     : TemporalGoal conflicts across horizons
  CH3 — Trust inflation: fraction of action classes auto-approved
  CH4 — Knowledge freshness: KG nodes with no edges updated in > 90d
  CH5 — Planning quality: sliding window of APDEVerifier scores

Publish results to EventBus under topic "cognitive.health" on each heartbeat.
"""
from __future__ import annotations
import dataclasses
import logging
import time
from typing import Any

log = logging.getLogger("essence.intelligence.cognitive_health")

_MEMORY_DRIFT_DAYS   = 60
_KG_FRESHNESS_DAYS   = 90
_TRUST_AUTO_THRESHOLD = 0.95
_PLANNING_WINDOW     = 20


@dataclasses.dataclass
class CognitiveHealthReport:
    memory_drift_score:    float        # 0.0 = fresh, 1.0 = completely stale
    goal_drift_score:      float        # 0.0 = coherent, 1.0 = contradictory
    trust_inflation_score: float        # fraction of action_classes marked "auto"
    knowledge_freshness:   float        # fraction of twin entries updated < 30d
    planning_quality_trend: float       # running average of apde_verifier scores
    alerts:                list[str]
    assessed_at:           float

    def is_healthy(self) -> bool:
        return (
            self.memory_drift_score    < 0.6
            and self.goal_drift_score  < 0.6
            and self.trust_inflation_score < 0.8
            and self.knowledge_freshness   > 0.3
            and (self.planning_quality_trend >= 0.5
                 or self.planning_quality_trend == -1.0)   # -1 = no data yet
        )

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class CognitiveHealthMonitor:
    """
    Continuously assesses the cognitive integrity of the kernel.

    All dependencies (twin, temporal, trust_ledger, kg, sqe_sampler) are
    optional — the monitor degrades gracefully when subsystems are absent.
    """

    def __init__(
        self,
        twin:        Any = None,
        temporal:    Any = None,
        trust_ledger: Any = None,
        kg:          Any = None,
        sqe_sampler: Any = None,
    ) -> None:
        self._twin        = twin
        self._temporal    = temporal
        self._trust       = trust_ledger
        self._kg          = kg
        self._sqe         = sqe_sampler
        self._score_window: list[float] = []

    # ── public API ──────────────────────────────────────────────────────────

    def assess(self) -> CognitiveHealthReport:
        """Run all five checks and return a CognitiveHealthReport."""
        alerts: list[str] = []
        now = time.time()

        mem_drift  = self._check_memory_drift()
        goal_drift = self._check_goal_drift()
        trust_inf  = self._check_trust_inflation()
        kg_fresh   = self._check_knowledge_freshness()
        plan_qual  = self._check_planning_quality()

        if mem_drift > 0.6:
            alerts.append(
                f"CH1: memory_drift={mem_drift:.2f} — PersonalTwin beliefs are stale")
        if goal_drift > 0.6:
            alerts.append(
                f"CH2: goal_drift={goal_drift:.2f} — TemporalGoals may conflict")
        if trust_inf > 0.8:
            alerts.append(
                f"CH3: trust_inflation={trust_inf:.2f} — auto-approval rate too high")
        if kg_fresh < 0.3:
            alerts.append(
                f"CH4: knowledge_freshness={kg_fresh:.2f} — KG nodes are stale")
        if 0.0 <= plan_qual < 0.5:
            alerts.append(
                f"CH5: planning_quality={plan_qual:.2f} — verifier scores declining")

        if alerts:
            log.warning("cognitive_health_alerts", extra={"alerts": alerts})

        return CognitiveHealthReport(
            memory_drift_score     = mem_drift,
            goal_drift_score       = goal_drift,
            trust_inflation_score  = trust_inf,
            knowledge_freshness    = kg_fresh,
            planning_quality_trend = plan_qual,
            alerts                 = alerts,
            assessed_at            = now,
        )

    def record_planning_score(self, score: float) -> None:
        """Feed an APDEVerifier score into the sliding window (CH5)."""
        self._score_window.append(score)
        if len(self._score_window) > _PLANNING_WINDOW:
            self._score_window.pop(0)

    # ── private checks ───────────────────────────────────────────────────────

    def _check_memory_drift(self) -> float:
        """CH1: fraction of PersonalTwin entries not updated in > MEMORY_DRIFT_DAYS."""
        if self._twin is None:
            return 0.0
        try:
            cutoff = time.time() - _MEMORY_DRIFT_DAYS * 86400
            data = getattr(self._twin, "_data", {})
            total = stale = 0
            for axis, entries in data.items():
                if axis.startswith("_"):
                    continue
                if not isinstance(entries, dict):
                    continue
                for key, val in entries.items():
                    total += 1
                    last_confirmed: float = 0.0
                    if isinstance(val, dict):
                        last_confirmed = float(val.get("last_confirmed", 0.0))
                    if last_confirmed < cutoff:
                        stale += 1
            return (stale / total) if total else 0.0
        except Exception as exc:
            log.debug("cognitive_health_memory_drift_error: %s", exc)
            return 0.0

    def _check_goal_drift(self) -> float:
        """CH2: detect horizon conflicts in TemporalGoals."""
        if self._temporal is None:
            return 0.0
        try:
            goals = self._temporal.all_goals()
            if not goals:
                return 0.0
            conflicts = 0
            for i, g1 in enumerate(goals):
                for g2 in goals[i + 1:]:
                    if g1.get("horizon") != g2.get("horizon"):
                        continue
                    d1 = str(g1.get("description", "")).lower()
                    d2 = str(g2.get("description", "")).lower()
                    _CONFLICT_PAIRS = [
                        ("reduce", "increase"), ("stop", "start"),
                        ("delete", "keep"), ("remove", "add"),
                    ]
                    for w1, w2 in _CONFLICT_PAIRS:
                        if (w1 in d1 and w2 in d2) or (w2 in d1 and w1 in d2):
                            conflicts += 1
                            break
            total_pairs = max(len(goals) * (len(goals) - 1) // 2, 1)
            return min(1.0, conflicts / total_pairs)
        except Exception as exc:
            log.debug("cognitive_health_goal_drift_error: %s", exc)
            return 0.0

    def _check_trust_inflation(self) -> float:
        """CH3: fraction of action_classes where autonomy level is 'auto'."""
        if self._trust is None:
            return 0.0
        try:
            ledger = getattr(self._trust, "_ledger", {})
            if not ledger:
                return 0.0
            auto_count = sum(
                1 for entry in ledger.values()
                if isinstance(entry, dict)
                and entry.get("autonomy_level") == "auto"
            )
            return auto_count / len(ledger)
        except Exception as exc:
            log.debug("cognitive_health_trust_inflation_error: %s", exc)
            return 0.0

    def _check_knowledge_freshness(self) -> float:
        """CH4: fraction of KG nodes with an edge updated within 90 days."""
        if self._kg is None:
            return 1.0   # no KG → no staleness concern
        try:
            cutoff = time.time() - _KG_FRESHNESS_DAYS * 86400
            nodes = getattr(self._kg, "_nodes", {})
            if not nodes:
                return 1.0
            edges = getattr(self._kg, "_edges", [])
            fresh_nodes: set = set()
            for edge in edges:
                ts = getattr(edge, "created_at", 0.0)
                if ts >= cutoff:
                    fresh_nodes.add(getattr(edge, "src", None))
                    fresh_nodes.add(getattr(edge, "dst", None))
            fresh_nodes.discard(None)
            return len(fresh_nodes) / len(nodes)
        except Exception as exc:
            log.debug("cognitive_health_kg_freshness_error: %s", exc)
            return 1.0

    def _check_planning_quality(self) -> float:
        """CH5: sliding-window average of APDEVerifier scores (-1 if no data)."""
        if not self._score_window:
            if self._sqe is not None:
                try:
                    recent = getattr(self._sqe, "_recent_scores", [])
                    if recent:
                        return sum(recent[-_PLANNING_WINDOW:]) / len(
                            recent[-_PLANNING_WINDOW:])
                except Exception:
                    pass
            return -1.0
        return sum(self._score_window) / len(self._score_window)
