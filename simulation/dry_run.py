"""DryRunSimulator — pre-execution consequence predictor.

Applies a rule library to task descriptions and returns a SimReport
without executing anything.

risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
safe_to_proceed: True for LOW and MEDIUM only.
"""
from __future__ import annotations
import dataclasses, re, time
from typing import Any


@dataclasses.dataclass
class SimReport:
    risk_level:        str
    predicted_effects: list[str]
    blocked_tasks:     list[str]
    advice:            str
    simulated_at:      float = dataclasses.field(default_factory=time.time)

    @property
    def safe_to_proceed(self) -> bool:
        return self.risk_level in ("LOW", "MEDIUM")


_DANGER_PATTERNS: list[tuple[str, str, str]] = [
    (r"delete|remove|drop|truncate|rm\s+-rf", "HIGH",    "Irreversible data deletion"),
    (r"format|wipe|erase",                    "CRITICAL", "Disk/storage wipe"),
    (r"send.*email|post.*message|publish",    "MEDIUM",   "Outbound communication"),
    (r"transfer.*funds|pay.*invoice",         "CRITICAL", "Financial transaction"),
    (r"overwrite|replace.*file",              "MEDIUM",   "File content replacement"),
    (r"uninstall|pip\s+uninstall",            "MEDIUM",   "Package removal"),
]
_LEVELS = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


class DryRunSimulator:
    def simulate(self, plan: Any,
                 task_descriptions: list[str] | None = None) -> SimReport:
        descs: list[tuple[str, str]] = []
        if task_descriptions:
            descs = [(str(i), d) for i, d in enumerate(task_descriptions)]
        elif hasattr(plan, "tasks"):
            for t in plan.tasks.values():
                descs.append((t.id, getattr(t, "description", str(t))))

        effects: list[str] = []
        blocked: list[str] = []
        highest = "LOW"

        for task_id, desc in descs:
            for pattern, level, effect in _DANGER_PATTERNS:
                if re.search(pattern, desc, re.IGNORECASE):
                    effects.append(f"[{task_id}] {effect}: '{desc[:80]}'")
                    if _LEVELS[level] > _LEVELS[highest]:
                        highest = level
                    if level in ("HIGH", "CRITICAL"):
                        blocked.append(task_id)

        advice = {
            "LOW":      "Plan appears safe. Proceed.",
            "MEDIUM":   "Minor risk. Review flagged tasks.",
            "HIGH":     "Dangerous operations detected. Require explicit approval.",
            "CRITICAL": "CRITICAL risk. Execution blocked.",
        }[highest]

        return SimReport(highest, effects, list(set(blocked)), advice)
