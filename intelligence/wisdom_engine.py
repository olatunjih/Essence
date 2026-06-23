"""WisdomEngine — long-term goal coherence and wellbeing reasoner.

Asks "Should I?" rather than "Can I?"
Checks: W1 goal coherence, W2 workload sustainability,
        W3 value alignment, W4 dependency risk.
Verdict levels: ENDORSE | CAUTION | WARN | OBJECT
"""
from __future__ import annotations
import dataclasses, time
from typing import Any


@dataclasses.dataclass
class WisdomVerdict:
    level:   str
    checks:  list[str]
    advice:  str
    ts:      float = dataclasses.field(default_factory=time.time)

    @property
    def should_proceed(self) -> bool:
        return self.level in ("ENDORSE", "CAUTION")


class WisdomEngine:
    def __init__(self, twin: Any = None) -> None:
        self._twin = twin

    def evaluate(self, action_description: str,
                 context: dict | None = None) -> WisdomVerdict:
        warnings: list[str] = []
        context = context or {}

        hours = context.get("estimated_daily_hours", 0)
        if hours > 12:
            warnings.append(f"W2: {hours}h/day is unsustainable")

        if self._twin:
            primary_goal = self._twin.get("goals", "primary", "")
            if (primary_goal
                    and "rest" in action_description.lower()
                    and "work" in primary_goal.lower()):
                warnings.append("W1: Rest conflicts with stated primary goal")

        if any(kw in action_description.lower()
               for kw in ("invest all", "transfer all", "liquidate")):
            warnings.append("W3: All-or-nothing financial action — high regret risk")

        if ("only" in action_description.lower()
                and any(kw in action_description.lower()
                        for kw in ("provider", "vendor", "service", "api"))):
            warnings.append("W4: Single-provider dependency detected")

        if not warnings:
            return WisdomVerdict("ENDORSE", ["All wisdom checks passed."], "Proceed.")
        if len(warnings) == 1:
            return WisdomVerdict("CAUTION", warnings, f"Caution: {warnings[0]}")
        if len(warnings) == 2:
            return WisdomVerdict("WARN", warnings,
                                 "Multiple concerns. Review before proceeding.")
        return WisdomVerdict("OBJECT", warnings,
                             "Strongly advise against. Multiple long-term risks.")
