"""KnowledgeGapDetector — proactively fills PersonalTwin knowledge gaps.

Scans PersonalTwin belief axes for low-confidence or missing entries, then
surfaces one targeted clarifying question per session via ProactiveEngine.
Confirmed answers are written back to PersonalTwin.

Runs on heartbeat tick; surfaces questions through ProactiveEngine;
writes confirmed answers back to PersonalTwin on user confirmation.

Persistence: <workspace>/identity/knowledge_gaps.json
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.intelligence.knowledge_gap_detector")


# ── Gap templates per axis ────────────────────────────────────────────────────
# Each entry: (key, question_template, importance_weight)
# weight 1.0 = highest priority

_AXIS_GAPS: dict[str, list[tuple[str, str, float]]] = {
    "goals": [
        ("primary_goal",  "What is your most important goal right now?",         1.0),
        ("deadline",      "Do any of your current goals have a specific deadline?", 0.8),
    ],
    "habits": [
        ("sleep_schedule", "What time do you typically go to sleep and wake up?",  0.7),
        ("work_start",     "What time do you usually start working each day?",     0.7),
    ],
    "skills": [
        ("strongest_skill", "What skill or tool would you say you know best?",    0.9),
        ("learning_focus",  "What skill are you actively trying to improve?",     0.9),
    ],
    "career": [
        ("role",           "What is your current role or occupation?",            1.0),
        ("focus_area",     "What domain or industry are you focused on?",         0.8),
    ],
    "health": [
        ("exercise_freq",  "How often do you exercise per week?",                 0.6),
    ],
    "learning": [
        ("interests",      "What topics are you most curious about right now?",   0.9),
        ("learning_style", "Do you prefer reading, watching, or hands-on practice?", 0.6),
    ],
    "financial": [
        ("budget_awareness", "Are you currently tracking a monthly budget?",      0.7),
    ],
    "relationships": [
        ("collaboration",  "Do you work primarily alone or as part of a team?",   0.6),
    ],
    "preferences": [
        ("timezone",       "What timezone are you in?",                           0.8),
        ("language",       "What is your preferred language for responses?",      0.8),
    ],
    "values": [
        ("core_value",     "What do you value most in your work — speed, quality, or creativity?", 0.7),
    ],
    "personality": [
        ("work_style",     "Do you prefer detailed step-by-step guidance or high-level overviews?", 0.8),
    ],
    "decision_patterns": [
        ("risk_tolerance", "How risk-tolerant are you when making decisions — cautious or bold?",   0.7),
    ],
}

_LOW_CONFIDENCE_THRESHOLD = 0.45   # beliefs below this count as gaps
_SESSION_GAP_SECONDS      = 3600   # ask at most one question per session (1 h)


@dataclasses.dataclass
class KnowledgeGap:
    """A single detected knowledge gap."""
    axis:        str
    key:         str
    question:    str
    importance:  float
    detected_at: float


class KnowledgeGapDetector:
    """
    Detects gaps in the PersonalTwin and surfaces targeted questions.

    Integration points
    ------------------
    Boot      : instantiated after PersonalTwin
    Heartbeat : call scan() each tick; result is published via event_bus
    Kernel.ingest_capsule : call confirm(axis, key, value) when the user's
                            message clearly answers a pending question
    """

    def __init__(self, workspace: Path,
                 twin:      Any = None,
                 event_bus: Any = None) -> None:
        self._ws       = workspace
        self._twin     = twin
        self._bus      = event_bus
        self._path     = workspace / "identity" / "knowledge_gaps.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict = self._load()

    # ── public API ────────────────────────────────────────────────────────────

    def scan(self) -> KnowledgeGap | None:
        """
        Scan the PersonalTwin for the highest-priority knowledge gap.
        Returns one gap to ask about (rate-limited to once per session),
        or None if no gap is pending.
        """
        now = time.time()
        last_asked = self._state.get("last_asked", 0.0)
        if now - last_asked < _SESSION_GAP_SECONDS:
            return None   # already asked this session

        gap = self._find_top_gap()
        if gap is None:
            return None

        self._state["last_asked"]    = now
        self._state["pending_axis"]  = gap.axis
        self._state["pending_key"]   = gap.key
        self._state["pending_q"]     = gap.question
        self._save()

        self._publish_question(gap)
        log.info("knowledge_gap_surfaced",
                 extra={"axis": gap.axis, "key": gap.key})
        return gap

    def confirm(self, answer: str) -> bool:
        """
        Attempt to extract a meaningful answer and write it back to
        PersonalTwin.  Called after user messages — returns True if
        the pending gap was resolved.
        """
        axis = self._state.get("pending_axis")
        key  = self._state.get("pending_key")
        if not axis or not key:
            return False

        answer = answer.strip()
        if len(answer) < 2:
            return False

        if self._twin is not None:
            try:
                self._twin.update(axis, key, answer, source="observed",
                                  confidence_delta=0.2)
                log.info("knowledge_gap_filled",
                         extra={"axis": axis, "key": key, "value": answer[:60]})
            except Exception as exc:
                log.debug("knowledge_gap_twin_update_error: %s", exc)
                return False

        # Clear pending state
        self._state.pop("pending_axis", None)
        self._state.pop("pending_key", None)
        self._state.pop("pending_q", None)
        self._save()
        return True

    def pending_question(self) -> str | None:
        """Return the currently pending question text, if any."""
        return self._state.get("pending_q")

    def all_gaps(self) -> list[KnowledgeGap]:
        """Return all currently detectable gaps (for debugging/audit)."""
        return self._find_all_gaps()

    # ── internal ──────────────────────────────────────────────────────────────

    def _find_top_gap(self) -> KnowledgeGap | None:
        gaps = self._find_all_gaps()
        if not gaps:
            return None
        return max(gaps, key=lambda g: g.importance)

    def _find_all_gaps(self) -> list[KnowledgeGap]:
        now  = time.time()
        gaps: list[KnowledgeGap] = []
        for axis, templates in _AXIS_GAPS.items():
            for key, question, importance in templates:
                if self._is_gap(axis, key):
                    gaps.append(KnowledgeGap(
                        axis=axis, key=key, question=question,
                        importance=importance, detected_at=now,
                    ))
        return gaps

    def _is_gap(self, axis: str, key: str) -> bool:
        """Return True if axis/key is missing or has low confidence."""
        if self._twin is None:
            return True
        try:
            belief = self._twin.get_belief(axis, key)
            if belief is None:
                return True
            return belief.confidence < _LOW_CONFIDENCE_THRESHOLD
        except Exception:
            return True

    def _publish_question(self, gap: KnowledgeGap) -> None:
        """Publish the clarifying question via event_bus."""
        if self._bus is None:
            return
        try:
            from essence.agents.proactive import WebhookEvent
            evt = WebhookEvent(
                source="knowledge_gap",
                event_type="question",
                payload={
                    "axis":     gap.axis,
                    "key":      gap.key,
                    "question": gap.question,
                },
            )
            pub = getattr(self._bus, "publish", None)
            if callable(pub):
                pub(evt)
        except Exception as exc:
            log.debug("knowledge_gap_publish_error: %s", exc)

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save(self) -> None:
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:
            log.debug("knowledge_gap_save_error: %s", exc)
