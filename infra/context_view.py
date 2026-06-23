
"""
Context Window Manager: ContextView resolver (Axiom A3).
Produces quarantined views; rejects out-of-scope reads at runtime.
"""
from __future__ import annotations
from typing import Any
from essence.apde_types import ContextView, Task, ContextScopeError


class ContextWindowManager:
    """
    Manages per-task ContextView objects.
    Ensures tasks can only read keys declared in their reads list.
    """

    def __init__(self, data_registry: dict[str, Any] | None = None) -> None:
        self._registry: dict[str, Any] = data_registry or {}

    def register(self, key: str, value: Any) -> None:
        """Register a data key in the global registry."""
        self._registry[key] = value

    def resolve(self, task: Task) -> ContextView:
        """
        Build a quarantined ContextView for the given task.
        Only keys in task.reads are included. Reads of unregistered keys
        are silently absent (None) — the task must handle absence.
        Out-of-scope reads raise ContextScopeError at call time.
        """
        view = ContextView(
            task_id=task.id,
            allowed_reads=list(task.reads),
        )
        view.load(self._registry)
        return view

    def assert_in_scope(self, task: Task, key: str) -> None:
        """Raise ContextScopeError if key is not in task.reads."""
        if key not in task.reads:
            raise ContextScopeError(
                f"Task {task.id} attempted out-of-scope read of '{key}' "
                f"(declared reads: {task.reads})")
