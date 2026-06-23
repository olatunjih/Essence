
"""PMP 5-phase pipeline: diff → disposition → scratch → commit → summary."""
from __future__ import annotations
import dataclasses as _dc, time
from typing import Any
from essence.apde_types import (
    IntentCapsule, PlanDAG, TaskState, AxiomViolation,
)
from essence.channels.pmp.intent_diff import IntentDiff
from essence.channels.pmp.dispositions import DispositionMatrix
from essence.channels.pmp.scratch import ScratchNamespace
from essence.channels.pmp.disposition_summary import DispositionSummary


@_dc.dataclass
class PMPResult:
    mutation_class:  str
    action:          str
    event_id:        str
    old_capsule_id:  str
    new_capsule_id:  str
    scratch_path:    str
    committed:       bool
    summary:         str


class PMPPipeline:
    """
    5-phase PMP pipeline.
    Phase 1: diff
    Phase 2: disposition lookup
    Phase 3: write to scratch
    Phase 4: commit (gated by pre_pmp_commit guardrail)
    Phase 5: render disposition summary
    """

    def __init__(self, guardrail_layer: Any,
                 delta_ledger: Any,
                 capsule_history_ring: list[IntentCapsule],
                 scratch_dir: str = "scratch") -> None:
        self._guardrails  = guardrail_layer
        self._ledger      = delta_ledger
        self._history     = capsule_history_ring   # capacity 4 for REVERT
        self._scratch     = ScratchNamespace(scratch_dir)
        self._matrix      = DispositionMatrix()
        self._summary_rdr = DispositionSummary()

    def run(self, old_capsule: IntentCapsule, new_capsule: IntentCapsule,
            current_plan: PlanDAG) -> PMPResult:
        """Execute 5 phases. Returns PMPResult."""
        import secrets as _sec

        # Phase 1: diff
        diff = IntentDiff.compute(old_capsule, new_capsule)
        event_id = _sec.token_hex(8)

        # Phase 2: disposition
        # For multi-task plans use the worst-case task state
        if current_plan.tasks:
            state_priority = {
                TaskState.FAILED: 0, TaskState.ACTIVE: 1,
                TaskState.DONE_INSUFFICIENT: 2,
                TaskState.READY: 3, TaskState.DONE: 4,
            }
            worst_task = min(
                current_plan.tasks,
                key=lambda t: state_priority.get(t.state, 99)
            )
            task_state = worst_task.state
        else:
            task_state = TaskState.READY

        action = self._matrix.lookup(diff.mutation_class, task_state)

        # Phase 3: write to scratch
        scratch_path = self._scratch.write(
            event_id=event_id,
            mutation_class=diff.mutation_class,
            action=action,
            old_capsule=old_capsule.to_dict(),
            new_capsule=new_capsule.to_dict(),
        )

        # Phase 4: commit (gated by guardrail)
        self._guardrails.pre_pmp_commit(
            mutation_class=diff.mutation_class,
            payload={"artifacts": new_capsule.artifacts},
        )
        # Append to delta ledger
        self._ledger.append(
            plan_id=current_plan.id,
            delta_type=diff.mutation_class,
            payload={
                "event_id":      event_id,
                "action":        action,
                "old_capsule":   old_capsule.id,
                "new_capsule":   new_capsule.id,
                "task_state":    task_state.value,
            },
        )
        # Update capsule history ring
        self._history.append(old_capsule)
        if len(self._history) > 4:
            self._history.pop(0)

        # Phase 5: summary
        summary = self._summary_rdr.render(
            diff=diff, action=action, event_id=event_id,
            retention_deadline=time.time() + 86400 * 7,
        )

        return PMPResult(
            mutation_class=diff.mutation_class,
            action=action,
            event_id=event_id,
            old_capsule_id=old_capsule.id,
            new_capsule_id=new_capsule.id,
            scratch_path=scratch_path,
            committed=True,
            summary=summary,
        )
