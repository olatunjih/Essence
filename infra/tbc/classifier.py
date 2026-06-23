
"""TBC: subscribes to PMP events + SQE deltas; emits replan boundary events."""
from __future__ import annotations
from typing import Callable


class TBCClassifier:
    """
    Task Boundary Classifier.
    Subscribes to PMP mutation events and SQE regime_change events.
    Emits a forced_replan_boundary event that the scheduler treats as a replan trigger.
    """

    def __init__(self) -> None:
        self._listeners: list[Callable[[str, dict], None]] = []

    def on_forced_replan(self, listener: Callable[[str, dict], None]) -> None:
        """Subscribe to forced replan boundary events."""
        self._listeners.append(listener)

    def on_pmp_event(self, event_type: str, data: dict) -> None:
        """Called when PMP emits a mutation event."""
        # PMP mutations that force replan: PIVOT, REFRAME, EXPAND, REVERT_EXPAND
        trigger_classes = {"PIVOT", "REFRAME", "EXPAND", "REVERT_EXPAND"}
        mutation_class = data.get("mutation_class", "")
        if mutation_class in trigger_classes:
            action = data.get("action", "")
            if "replan" in action:
                self._fire("pmp_mutation", {
                    "mutation_class": mutation_class,
                    "action":        action,
                    "source":        "pmp",
                })

    def on_sqe_event(self, event_type: str, data: dict) -> None:
        """Called when SQE emits a regime_change event."""
        if event_type == "regime_change":
            self._fire("sqe_regime_change", {**data, "source": "sqe"})

    def _fire(self, reason: str, data: dict) -> None:
        for fn in self._listeners:
            fn("forced_replan_boundary", {"reason": reason, **data})
