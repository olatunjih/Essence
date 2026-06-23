
"""PMP Phase 5: disposition summary renderer."""
from __future__ import annotations
import time
from essence.channels.pmp.intent_diff import IntentDiff


class DispositionSummary:
    """
    Renders a human-readable disposition summary for a PMP mutation.
    Includes the retention deadline so users know the revert window.
    """

    def render(self, diff: IntentDiff, action: str, event_id: str,
               retention_deadline: float) -> str:
        deadline_str = time.strftime(
            "%Y-%m-%d %H:%M UTC", time.gmtime(retention_deadline))
        lines = [
            f"=== PMP Mutation Summary (event: {event_id}) ===",
            f"Mutation class : {diff.mutation_class}",
            f"Action         : {action}",
            f"Goal changed   : {'yes' if diff.goal_changed else 'no'}",
            f"Signals delta  : {diff.signals_delta:+d}",
            f"Artifacts delta: {diff.artifacts_delta:+d}",
            f"Revert deadline: {deadline_str}",
            "=" * 48,
        ]
        return "\n".join(lines)
