"""EmotionalStateTracker — infers user sentiment and stress from message patterns.

Analyses message urgency words, length bursts, question density, and punctuation
to produce an EmotionalState that is injected as a tone modifier into the agent's
system prompt alongside UserPreferenceEngine.

Called in ingest_capsule() alongside UserPreferenceEngine.observe().
Injects a tone modifier into the LLM system prompt via system_hint().
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.intelligence.emotional_state")


# ── Lexicon ───────────────────────────────────────────────────────────────────

_URGENCY_WORDS = frozenset({
    "urgent", "asap", "immediately", "critical", "emergency",
    "now", "right now", "fix this", "broken", "crash", "outage",
    "deadline", "overdue", "stuck", "blocked", "help",
})

_FRUSTRATION_WORDS = frozenset({
    "frustrated", "annoying", "useless", "wrong", "terrible", "awful",
    "hate", "stupid", "ridiculous", "unacceptable", "worse", "fail",
    "failed", "again", "still", "not working", "doesn't work",
})

_POSITIVE_WORDS = frozenset({
    "great", "thanks", "thank you", "perfect", "awesome", "excellent",
    "love it", "works", "solved", "done", "yes", "cool", "nice",
})

_CALM_WORDS = frozenset({
    "please", "whenever", "no rush", "take your time", "at your convenience",
    "eventually", "low priority",
})


@dataclasses.dataclass
class EmotionalState:
    """Current inferred emotional state of the user."""
    sentiment:       str    # "positive" | "neutral" | "stressed" | "frustrated"
    stress_level:    float  # 0.0 (calm) → 1.0 (highly stressed)
    urgency:         float  # 0.0 → 1.0
    frustration:     float  # 0.0 → 1.0
    positivity:      float  # 0.0 → 1.0
    assessed_at:     float

    def tone_modifier(self) -> str:
        """Return a compact system-prompt modifier for the LLM."""
        if self.sentiment == "frustrated":
            return (
                "[TONE] User appears frustrated. "
                "Be concise, direct, and skip preamble. "
                "Acknowledge the issue immediately before explaining. "
                "Avoid filler phrases."
            )
        if self.sentiment == "stressed":
            return (
                "[TONE] User appears stressed or urgent. "
                "Lead with the answer, then explain. "
                "Be efficient — no lengthy context-setting."
            )
        if self.sentiment == "positive":
            return (
                "[TONE] User is in a positive mood. "
                "Maintain warm, collaborative tone."
            )
        return ""   # neutral — no override

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class EmotionalStateTracker:
    """
    Infers user sentiment from message patterns using a lightweight
    rule-based approach (no LLM calls).

    Signal sources:
      • Urgency vocabulary        → urgency score
      • Frustration vocabulary    → frustration score
      • Positive vocabulary       → positivity score
      • Question density (many ??) → mild stress indicator
      • Message length burst       → urgency indicator (very short = terse)
      • Punctuation density (!!!)  → emotional intensity
      • Capitalisation ratio (CAPS) → frustration/urgency

    State is maintained as an exponential moving average so a single
    frustrated message does not permanently label the user.

    Integration points
    ------------------
    Boot         : instantiated after UserPreferenceEngine
    ingest_capsule: call observe(raw_prompt) on every user message
    Agent._sys   : call system_hint() to prepend tone modifier to system prompt
    """

    _EMA_ALPHA = 0.25   # faster decay than preference (emotional states reset quickly)
    _WINDOW    = 10     # rolling window size for burst detection

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "identity" / "emotional_state.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lengths: deque[int] = deque(maxlen=self._WINDOW)
        self._urgency_ema     = 0.0
        self._frustration_ema = 0.0
        self._positivity_ema  = 0.0
        self._state: EmotionalState | None = None
        self._load()

    # ── public API ────────────────────────────────────────────────────────────

    def observe(self, message: str) -> EmotionalState:
        """
        Update emotional model from a new user message.
        Returns the updated EmotionalState.
        """
        msg_lower  = message.lower()
        words      = re.findall(r"\b[a-z']+\b", msg_lower)
        word_set   = set(words)
        word_count = len(words) or 1

        # --- vocabulary scores (0.0–1.0) ---
        urgency_hits      = len(word_set & _URGENCY_WORDS)
        frustration_hits  = len(word_set & _FRUSTRATION_WORDS)
        positive_hits     = len(word_set & _POSITIVE_WORDS)
        calm_hits         = len(word_set & _CALM_WORDS)

        urgency_raw     = min(1.0, urgency_hits     / 3.0)
        frustration_raw = min(1.0, frustration_hits / 2.0)
        positivity_raw  = min(1.0, positive_hits    / 2.0)

        # Calm dampens urgency/frustration
        calm_factor = max(0.0, 1.0 - calm_hits * 0.3)
        urgency_raw     *= calm_factor
        frustration_raw *= calm_factor

        # --- punctuation signals ---
        excl_ratio  = message.count("!") / word_count
        _q_ratio    = message.count("?") / word_count
        caps_ratio  = sum(1 for c in message if c.isupper()) / max(len(message), 1)

        urgency_raw     = min(1.0, urgency_raw     + excl_ratio * 0.4)
        frustration_raw = min(1.0, frustration_raw + caps_ratio  * 0.5)

        # Multi-question = mild stress
        if message.count("?") >= 3:
            urgency_raw = min(1.0, urgency_raw + 0.15)

        # --- length burst: very short (< 5 words) after normal = terse / urgent ---
        self._lengths.append(word_count)
        if len(self._lengths) >= 3:
            avg_prev = sum(list(self._lengths)[:-1]) / max(len(self._lengths) - 1, 1)
            if word_count < 5 and avg_prev > 12:
                urgency_raw = min(1.0, urgency_raw + 0.2)

        # --- EMA update ---
        a = self._EMA_ALPHA
        self._urgency_ema     = (1 - a) * self._urgency_ema     + a * urgency_raw
        self._frustration_ema = (1 - a) * self._frustration_ema + a * frustration_raw
        self._positivity_ema  = (1 - a) * self._positivity_ema  + a * positivity_raw

        stress_level = max(self._urgency_ema, self._frustration_ema)

        # --- classify sentiment ---
        if self._frustration_ema > 0.35:
            sentiment = "frustrated"
        elif stress_level > 0.3:
            sentiment = "stressed"
        elif self._positivity_ema > 0.4:
            sentiment = "positive"
        else:
            sentiment = "neutral"

        self._state = EmotionalState(
            sentiment    = sentiment,
            stress_level = round(stress_level, 3),
            urgency      = round(self._urgency_ema, 3),
            frustration  = round(self._frustration_ema, 3),
            positivity   = round(self._positivity_ema, 3),
            assessed_at  = time.time(),
        )
        self._save()
        if sentiment != "neutral":
            log.debug("emotional_state_updated",
                      extra={"sentiment": sentiment, "stress": round(stress_level, 2)})
        return self._state

    def current(self) -> EmotionalState | None:
        """Return the most recent EmotionalState (None if no messages observed)."""
        return self._state

    def system_hint(self) -> str:
        """Return tone modifier string for injection into the LLM system prompt."""
        if self._state is None:
            return ""
        return self._state.tone_modifier()

    def reset(self) -> None:
        """Reset all emotional state (e.g. after long session gap)."""
        self._urgency_ema     = 0.0
        self._frustration_ema = 0.0
        self._positivity_ema  = 0.0
        self._state = None
        self._save()

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._urgency_ema     = float(raw.get("urgency_ema", 0.0))
                self._frustration_ema = float(raw.get("frustration_ema", 0.0))
                self._positivity_ema  = float(raw.get("positivity_ema", 0.0))
                if "state" in raw and raw["state"]:
                    d = raw["state"]
                    self._state = EmotionalState(**d)
            except Exception:
                pass

    def _save(self) -> None:
        try:
            data = {
                "urgency_ema":     self._urgency_ema,
                "frustration_ema": self._frustration_ema,
                "positivity_ema":  self._positivity_ema,
                "state": self._state.to_dict() if self._state else None,
            }
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass
