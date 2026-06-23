
"""
Stage C — Ratification: tier-aware approval logic.
L0, L1: manual; L2: conditional auto-approve; L3: HIGH-risk downgrade rule.

Fix 3: RatificationResult now carries effective_risk (a per-task risk snapshot
after any tier-3 downgrade) so callers can inspect the effective risk without
the Ratifier mutating the Task objects in the PlanDAG.
"""
from __future__ import annotations
import dataclasses as _dc
from essence.apde_types import PlanDAG, RiskLevel, Task


@_dc.dataclass
class RatificationResult:
    """
    Result of a ratification decision.

    Attributes:
        approved:       Whether the plan is approved to proceed.
        tier:           Autonomy tier used for this decision.
        auto_approved:  True when no human approval was required.
        reason:         Human-readable explanation of the decision.
        effective_risk: Mapping of task_id -> effective RiskLevel after
                        any tier-3 HIGH→MEDIUM downgrade.  The PlanDAG
                        tasks are NOT mutated; callers should use this
                        dict to determine the effective risk for execution.
    """
    approved:       bool
    tier:           int
    auto_approved:  bool
    reason:         str
    effective_risk: dict[str, RiskLevel] = _dc.field(default_factory=dict)


class Ratifier:
    """
    Tier-aware plan ratification.

    L0 (tier 0): always manual — every plan requires human approval.
    L1 (tier 1): manual unless all tasks are LOW risk.
    L2 (tier 2): auto-approve if no HIGH/CRITICAL tasks; else manual.
    L3 (tier 3): auto-approve; records HIGH→MEDIUM downgrade in effective_risk
                 without mutating task.risk on the PlanDAG.

    The Ratifier NEVER mutates tasks in the plan.  Effective risk overrides
    for tier 3 are returned in RatificationResult.effective_risk so callers
    can apply them to execution context without altering the frozen plan.
    """

    def __init__(self, autonomy_tier: int,
                 human_approval_fn: "callable | None" = None) -> None:
        """
        Args:
            autonomy_tier:     Autonomy tier 0-3.  Values outside [0,3] are clamped.
            human_approval_fn: Optional callable(plan) -> bool for manual approval.
                               Defaults to auto-approve (returns True) for tests/dev.
        """
        self._tier     = max(0, min(3, autonomy_tier))
        self._human_fn = human_approval_fn or (lambda plan: True)

    def ratify(self, plan: PlanDAG) -> RatificationResult:
        """
        Ratify a plan per autonomy tier.

        Args:
            plan: The PlanDAG to ratify.  Tasks are read but never mutated.

        Returns:
            RatificationResult with approved, tier, auto_approved, reason,
            and effective_risk (populated for tier 3 only).
        """
        all_risks = [t.risk for t in plan.tasks]
        has_high  = any(r in (RiskLevel.HIGH, RiskLevel.CRITICAL) for r in all_risks)
        all_low   = all(r == RiskLevel.LOW for r in all_risks)

        if self._tier == 0:
            approved = bool(self._human_fn(plan))
            return RatificationResult(
                approved=approved, tier=0, auto_approved=False,
                reason="L0: always manual",
                effective_risk={t.id: t.risk for t in plan.tasks},
            )

        if self._tier == 1:
            if all_low:
                return RatificationResult(
                    approved=True, tier=1, auto_approved=True,
                    reason="L1: all tasks LOW risk",
                    effective_risk={t.id: t.risk for t in plan.tasks},
                )
            approved = bool(self._human_fn(plan))
            return RatificationResult(
                approved=approved, tier=1, auto_approved=False,
                reason="L1: manual (non-LOW tasks present)",
                effective_risk={t.id: t.risk for t in plan.tasks},
            )

        if self._tier == 2:
            if has_high:
                approved = bool(self._human_fn(plan))
                return RatificationResult(
                    approved=approved, tier=2, auto_approved=False,
                    reason="L2: manual (HIGH/CRITICAL present)",
                    effective_risk={t.id: t.risk for t in plan.tasks},
                )
            return RatificationResult(
                approved=True, tier=2, auto_approved=True,
                reason="L2: auto-approved (no HIGH/CRITICAL)",
                effective_risk={t.id: t.risk for t in plan.tasks},
            )

        # tier == 3: record HIGH→MEDIUM downgrade in effective_risk.
        # Task objects in the plan are NOT mutated.
        effective: dict[str, RiskLevel] = {}
        for task in plan.tasks:
            if task.risk == RiskLevel.HIGH:
                effective[task.id] = RiskLevel.MEDIUM
            else:
                effective[task.id] = task.risk

        return RatificationResult(
            approved=True, tier=3, auto_approved=True,
            reason="L3: HIGH downgraded to MEDIUM in effective_risk, auto-approved",
            effective_risk=effective,
        )
