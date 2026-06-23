"""UserStateDetector — infers cognitive/emotional state from request text.

States: FOCUSED | EXPLORING | FRUSTRATED | OVERLOADED | DECIDING | LEARNING | NEUTRAL
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from enum import Enum


class UserState(str, Enum):
    FOCUSED    = "focused"
    EXPLORING  = "exploring"
    FRUSTRATED = "frustrated"
    OVERLOADED = "overloaded"
    DECIDING   = "deciding"
    LEARNING   = "learning"
    NEUTRAL    = "neutral"


_RULES: list[tuple[str, UserState]] = [
    (r"not working|broken|keeps? failing|again|still|error|failed",
     UserState.FRUSTRATED),
    (r"too much|overwhelmed|so many|can.t keep up|confusing",
     UserState.OVERLOADED),
    (r"should i|which is better|pros and cons|compare|versus",
     UserState.DECIDING),
    (r"how does|explain|what is|teach me|help me understand",
     UserState.LEARNING),
    (r"just|simply|quickly|exactly|only",
     UserState.FOCUSED),
    (r"curious|wonder|explore|what if|could we",
     UserState.EXPLORING),
]
_CAPS = re.compile(r"\b[A-Z]{3,}\b")


@dataclass
class StateSignal:
    state:      UserState
    confidence: float
    cues:       list[str]


class UserStateDetector:
    def detect(self, text: str) -> StateSignal:
        scores: dict[UserState, float] = {s: 0.0 for s in UserState}
        cues:   list[str]              = []
        if len(_CAPS.findall(text)) >= 2:
            scores[UserState.FRUSTRATED] += 0.4
            cues.append("caps_burst")
        for pattern, state in _RULES:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                scores[state] += 0.35
                cues.append(m.group(0).strip())
        best  = max(scores, key=lambda s: scores[s])
        score = scores[best]
        if score < 0.2:
            best, score = UserState.NEUTRAL, 1.0
        return StateSignal(state=best, confidence=min(score, 1.0), cues=cues)

    def response_mode(self, signal: StateSignal) -> dict:
        modes = {
            UserState.FRUSTRATED: {"max_length": "short",  "tone": "reassuring"},
            UserState.OVERLOADED:  {"max_length": "brief",  "tone": "calm"},
            UserState.DECIDING:    {"max_length": "medium", "tone": "analytical"},
            UserState.LEARNING:    {"max_length": "long",   "tone": "didactic"},
            UserState.FOCUSED:     {"max_length": "short",  "tone": "direct"},
            UserState.EXPLORING:   {"max_length": "long",   "tone": "curious"},
            UserState.NEUTRAL:     {"max_length": "medium", "tone": "balanced"},
        }
        return modes[signal.state]
