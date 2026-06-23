
"""Axiom A6: check_disjointness() — write sets must not overlap."""
from __future__ import annotations
from essence.apde_types import Task, AxiomViolation


def check_disjointness(tasks: list[Task]) -> None:
    """
    Axiom A6 enforcement: raises AxiomViolation if any two tasks declare
    overlapping write sets.
    A write overlap is defined as two tasks declaring the same write key.
    """
    seen: dict[str, str] = {}
    for task in tasks:
        for w in task.writes:
            w_norm = w.lower().strip()
            if w_norm in seen:
                raise AxiomViolation(
                    f"Axiom A6 violated: write key '{w}' declared by both "
                    f"task '{seen[w_norm]}' and task '{task.id}'")
            seen[w_norm] = task.id
