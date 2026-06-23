
"""PMP Phase 2: Disposition matrix — 8 mutation classes × 5 task states."""
from __future__ import annotations
from essence.apde_types import TaskState

# 8 mutation classes × 5 task states → action
# Actions: "cancel_replan", "continue", "replan", "checkpoint_replan",
#          "revert", "abort", "no_op"
_MATRIX: dict[str, dict[str, str]] = {
    "EXPAND": {
        "READY":             "replan",
        "ACTIVE":            "checkpoint_replan",
        "DONE":              "no_op",
        "DONE_INSUFFICIENT": "replan",
        "FAILED":            "replan",
    },
    "CONTRACT": {
        "READY":             "replan",
        "ACTIVE":            "cancel_replan",
        "DONE":              "no_op",
        "DONE_INSUFFICIENT": "replan",
        "FAILED":            "replan",
    },
    "REFRAME": {
        "READY":             "replan",
        "ACTIVE":            "abort",
        "DONE":              "replan",
        "DONE_INSUFFICIENT": "replan",
        "FAILED":            "replan",
    },
    "PIVOT": {
        "READY":             "replan",
        "ACTIVE":            "abort",
        "DONE":              "replan",
        "DONE_INSUFFICIENT": "replan",
        "FAILED":            "replan",
    },
    "REFINE": {
        "READY":             "continue",
        "ACTIVE":            "continue",
        "DONE":              "no_op",
        "DONE_INSUFFICIENT": "continue",
        "FAILED":            "replan",
    },
    "REPAIR": {
        "READY":             "continue",
        "ACTIVE":            "checkpoint_replan",
        "DONE":              "no_op",
        "DONE_INSUFFICIENT": "replan",
        "FAILED":            "replan",
    },
    "SCOPE_LIMIT": {
        "READY":             "replan",
        "ACTIVE":            "cancel_replan",
        "DONE":              "no_op",
        "DONE_INSUFFICIENT": "replan",
        "FAILED":            "no_op",
    },
    "SCOPE_LIFT": {
        "READY":             "replan",
        "ACTIVE":            "checkpoint_replan",
        "DONE":              "replan",
        "DONE_INSUFFICIENT": "replan",
        "FAILED":            "replan",
    },
    "REVERT": {
        "READY":             "revert",
        "ACTIVE":            "abort",
        "DONE":              "revert",
        "DONE_INSUFFICIENT": "revert",
        "FAILED":            "revert",
    },
    "REVERT_EXPAND": {
        "READY":             "replan",
        "ACTIVE":            "abort",
        "DONE":              "replan",
        "DONE_INSUFFICIENT": "replan",
        "FAILED":            "replan",
    },
}


class DispositionMatrix:
    """
    8×5 disposition matrix (10 mutation classes including REVERT/REVERT_EXPAND).
    Lookup raises KeyError only if mutation_class or state is entirely unknown.
    """

    def lookup(self, mutation_class: str, task_state: TaskState) -> str:
        """Return the action string for (mutation_class, task_state)."""
        row = _MATRIX.get(mutation_class)
        if row is None:
            raise KeyError(
                f"Unknown mutation class: {mutation_class!r}. "
                f"Valid: {list(_MATRIX.keys())}")
        action = row.get(task_state.value)
        if action is None:
            raise KeyError(
                f"Unknown task state: {task_state.value!r} for "
                f"mutation class {mutation_class!r}")
        return action

    def all_classes(self) -> list[str]:
        return list(_MATRIX.keys())

    def all_states(self) -> list[str]:
        return [s.value for s in TaskState]
