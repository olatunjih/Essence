
"""PMP Phase 1: Intent diff — classify mutation class from capsule pair."""
from __future__ import annotations
import dataclasses as _dc
from essence.apde_types import IntentCapsule

_MUTATION_CLASSES = [
    "EXPAND", "CONTRACT", "REFRAME", "PIVOT", "REFINE",
    "REPAIR", "SCOPE_LIMIT", "SCOPE_LIFT", "REVERT", "REVERT_EXPAND",
]


@_dc.dataclass
class IntentDiff:
    mutation_class: str
    old_capsule_id: str
    new_capsule_id: str
    goal_changed:   bool
    signals_delta:  int
    artifacts_delta: int
    notes:          str = ""

    @classmethod
    def compute(cls, old: IntentCapsule, new: IntentCapsule) -> "IntentDiff":
        """Compute mutation class by diffing two capsules."""
        goal_changed    = old.goal.strip() != new.goal.strip()
        sig_delta       = len(new.success_signals) - len(old.success_signals)
        art_delta       = len(new.artifacts) - len(old.artifacts)
        old_tokens      = old.budget.get("tokens", 0)
        new_tokens      = new.budget.get("tokens", 0)

        mutation_class = _classify(
            goal_changed=goal_changed,
            sig_delta=sig_delta,
            art_delta=art_delta,
            budget_delta=new_tokens - old_tokens,
            old_id=old.id, new_id=new.id,
        )
        return cls(
            mutation_class=mutation_class,
            old_capsule_id=old.id,
            new_capsule_id=new.id,
            goal_changed=goal_changed,
            signals_delta=sig_delta,
            artifacts_delta=art_delta,
        )


def _classify(goal_changed: bool, sig_delta: int, art_delta: int,
              budget_delta: int, old_id: str, new_id: str) -> str:
    """Rule-based mutation class classifier."""
    if old_id == new_id:
        return "REVERT"
    if goal_changed and sig_delta > 0 and art_delta > 0:
        return "REVERT_EXPAND"
    if goal_changed and sig_delta > 2:
        return "PIVOT"
    if goal_changed:
        return "REFRAME"
    if sig_delta > 0 and art_delta > 0:
        return "EXPAND"
    if sig_delta < 0 and art_delta < 0:
        return "CONTRACT"
    if sig_delta > 0 and art_delta == 0:
        return "SCOPE_LIFT"
    if sig_delta < 0 and art_delta == 0:
        return "SCOPE_LIMIT"
    if art_delta == 0 and sig_delta == 0 and budget_delta < 0:
        return "REPAIR"
    if art_delta == 0 and sig_delta == 0:
        return "REFINE"
    return "REFINE"
