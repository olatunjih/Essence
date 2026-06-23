
"""SQE sampler: subscribes to verification outcomes, updates stratum store."""
from __future__ import annotations
from typing import Callable, Any
from essence.apde_types import Task, VerificationOutcome
from essence.infra.sqe.stratum_store import StratumStore


class SQESampler:
    """
    Receives verification deltas and updates SQE stratum statistics.
    Stratum key = "{rubric_id}:{task_risk}".
    Emits regime_change events when mean shifts significantly (>1 std dev).
    """

    def __init__(self, stratum_store: StratumStore,
                 regime_change_threshold: float = 1.0) -> None:
        self._store     = stratum_store
        self._threshold = regime_change_threshold
        self._listeners: list[Callable[[str, dict], None]] = []

    def on_regime_change(self, listener: Callable[[str, dict], None]) -> None:
        """Subscribe to regime change events."""
        self._listeners.append(listener)

    def record(self, task: Task, outcome: VerificationOutcome) -> None:
        """Record a verification outcome; emit regime_change if warranted."""
        key    = f"{outcome.rubric_id}:{task.risk.value}"
        before = self._store.get(key)
        self._store.update(key, outcome.score)
        after  = self._store.get(key)

        if before["n"] >= 3:
            shift = abs(after["mean"] - before["mean"])
            if before["std"] > 0 and shift > self._threshold * before["std"]:
                self._emit_regime_change(key, before, after)

    def _emit_regime_change(self, key: str, before: dict, after: dict) -> None:
        data = {"stratum_key": key, "before": before, "after": after}
        for fn in self._listeners:
            fn("regime_change", data)
