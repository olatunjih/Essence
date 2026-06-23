
"""Axiom A5: decidable covers() predicate."""
from __future__ import annotations
from essence.apde_types import Task


def covers(tasks: list[Task], success_signals: list[str]) -> bool:
    """
    Axiom A5 enforcement: returns True iff the union of task goals and
    writes collectively covers all success_signals.
    A signal is covered if it appears (case-insensitive substring) in any
    task goal or write declaration, or if no signals are declared.
    """
    if not success_signals:
        return True
    covered: set[str] = set()
    task_text = " ".join(
        t.goal.lower() + " " + " ".join(w.lower() for w in t.writes)
        for t in tasks
    )
    for sig in success_signals:
        sig_lower = sig.lower()
        # Direct substring match in combined task text
        if sig_lower in task_text:
            covered.add(sig)
            continue
        # Word-level match: all words of sig appear in task_text
        words = sig_lower.split()
        if all(w in task_text for w in words):
            covered.add(sig)
    return covered == set(success_signals)
