
"""Axiom A7: ToolBelt — least-privilege tool filter for a specific task."""
from __future__ import annotations
from essence.apde_types import Task, ToolRecord, AxiomViolation


class ToolBelt:
    """
    Least-privilege tool filter (A7).
    Given a task and a registry of ToolRecords, produces the minimal set
    of tools the task is permitted to use.
    Research-only tools are excluded from non-research capsules.
    """

    def __init__(self, all_records: list[ToolRecord],
                 research_context: bool = False) -> None:
        self._all      = all_records
        self._research = research_context

    def filter_for_task(self, task: Task) -> list[ToolRecord]:
        """
        Return only records whose tool_name appears in task.tools.
        If task.tools is empty, return all non-research records (safe default).
        Raise AxiomViolation if a research_only tool is requested in
        non-research context (Axiom 7).
        """
        requested = set(task.tools)
        result: list[ToolRecord] = []

        for rec in self._all:
            # If task declares explicit tools, only include requested
            if requested and rec.tool_name not in requested:
                continue
            # Axiom 7: research_only tools blocked in non-research context
            if rec.research_only and not self._research:
                if rec.tool_name in requested:
                    raise AxiomViolation(
                        f"Axiom A7 violated: tool '{rec.tool_name}' is "
                        f"research_only and cannot be used in execution context")
                continue
            result.append(rec)
        return result

    def schema_list(self, task: Task) -> list[dict]:
        """Return the OpenAI-compatible tool schema list for this task."""
        records = self.filter_for_task(task)
        return [{"type": "function", "function": {
            "name": r.tool_name,
            "description": f"[{r.cost_class}] {r.tool_name}",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }} for r in records]
