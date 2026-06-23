
"""Decision-Guide: rule selector with priority + id lexicographic conflict resolution."""
from __future__ import annotations
from essence.agents.decision_guide.indexes import RuleIndex
from essence.apde_types import Task, RiskLevel, GuidanceBlock

_RISK_PRIORITY = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


class RuleSelector:
    """
    Selects applicable Decision-Guide rules for a given task.
    Conflict resolution: priority (risk level) then id lexicographic order.
    """

    def __init__(self, index: RuleIndex) -> None:
        self._index = index

    def select(self, task: Task) -> list[dict]:
        """Return sorted rules applicable to this task."""
        candidates = self._index.composite(
            tools=task.tools,
            risk=task.risk.value,
            write_path=task.writes[0] if task.writes else "",
        )
        # Add risk-level rules
        for rule in self._index.by_risk(task.risk.value):
            if all(r["id"] != rule["id"] for r in candidates):
                candidates.append(rule)
        # Conflict resolution: sort by risk priority then id
        return sorted(
            candidates,
            key=lambda r: (
                _RISK_PRIORITY.get(r.get("risk", "LOW"), 3),
                r["id"],
            ),
        )

    def build_guidance(self, task: Task) -> GuidanceBlock:
        """Build a GuidanceBlock from selected rules."""
        rules = self.select(task)
        # checkpoint_every_pct from DG-016 for HIGH tasks
        checkpoint_pct = 0.25 if task.risk == RiskLevel.HIGH else 1.0
        return GuidanceBlock(
            rules=rules,
            risk=task.risk,
            checkpoint_every_pct=checkpoint_pct,
        )
