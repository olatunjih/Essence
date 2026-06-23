""" — token budget enforcement."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# TOKEN BUDGET ENFORCEMENT AT PROVIDER LEVEL 
# ══════════════════════════════════════════════════════════════════════════════
# CostTracker raises BudgetExceededError after the response — too late.
# BudgetGuardedProvider wraps ProviderChain.complete() and stops streaming
# the moment token spend crosses the per-task budget.
# ENV:  Essence_COST_BUDGET=50000  tokens per task (0 = unlimited)

class BudgetGuardedProvider:
    """
    Wraps a ProviderChain and enforces token budget mid-stream.
    Token counting: 4 chars ≈ 1 token (conservative estimate).
    When budget is reached, yields a budget-exceeded notice and stops.
    """

    def __init__(self, provider: Any, cost_tracker: Any,
                 task_id: str, budget: int = 0) -> None:
        self._prov      = provider
        self._tracker   = cost_tracker
        self._task_id   = task_id
        self._budget    = budget   # 0 = unlimited
        self._tokens    = 0

    def complete(self, messages: list[dict], **kw) -> "Iterator[str]":
        for tok in self._prov.complete(messages, **kw):
            self._tokens += max(1, len(tok) // 4)
            if self._budget > 0 and self._tokens >= self._budget:
                yield f"\n[Budget of {self._budget:,} tokens reached — stopping]"
                log.warning("budget_mid_stream_stop",
                            extra={"task": self._task_id,
                                   "tokens": self._tokens,
                                   "budget": self._budget})
                return
            yield tok

    def alive(self) -> bool:
        return self._prov.alive()

    @property
    def providers(self) -> list:
        return getattr(self._prov, "providers", [self._prov])


# ══════════════════════════════════════════════════════════════════════════════
