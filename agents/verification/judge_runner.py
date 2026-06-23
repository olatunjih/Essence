
"""LLMJudged routing: runs rubric-based verification via LLM judge pool."""
from __future__ import annotations
import json, re
from typing import Any
from essence.apde_types import CallClass, Task, ExecResult, VerificationOutcome


class JudgeRunner:
    """
    Executes rubric-based LLM-judged verification (Stage E).
    Calls the LLM router with call class VERIFY, pool judge_small.
    """

    _SYS = (
        "You are a strict quality judge. Given a task, its result, and a rubric, "
        "score each axis 0.0–1.0. Respond with JSON: "
        "{\"axes\": [{\"name\": ..., \"score\": ..., \"rationale\": ...}], "
        "\"overall\": float, \"passed\": bool}. No markdown. JSON only."
    )

    def __init__(self, llm_router: Any, rubric_registry: Any,
                 epoch_id: str) -> None:
        self._router   = llm_router
        self._registry = rubric_registry
        self._epoch_id = epoch_id

    def judge(self, task: Task, result: ExecResult,
              rubric_id: str) -> VerificationOutcome:
        """Run a rubric against a task result. Returns VerificationOutcome."""
        rubric = self._registry.get(rubric_id)

        prompt = json.dumps({
            "task_goal":  task.goal,
            "done_when":  task.done_when,
            "artifacts":  result.artifacts,
            "rubric_id":  rubric.id,
            "rubric_axes": [
                {"name": ax["name"], "description": ax.get("description", "")}
                for ax in rubric.axes
            ],
        }, indent=2)

        try:
            response = self._router.call(
                call_class=CallClass.VERIFY,
                messages=[
                    {"role": "system", "content": self._SYS},
                    {"role": "user",   "content": prompt},
                ],
                task_id=task.id,
                epoch_id=self._epoch_id,
                rubric_id=rubric_id,
                rubric_version=rubric.version,
            )
            cleaned = re.sub(r"```[a-zA-Z]*", "", response).strip()
            d = json.loads(cleaned)
        except Exception as e:
            # Fallback per rubric policy
            if rubric.fallback_on_judge_fail == "fail_closed":
                return VerificationOutcome(
                    task_id=task.id, rubric_id=rubric_id,
                    score=0.0, passed=False,
                    verdicts=[{"error": str(e)}],
                    notes=f"Judge failed (fail_closed): {e}",
                )
            else:  # escalate_to_human
                return VerificationOutcome(
                    task_id=task.id, rubric_id=rubric_id,
                    score=0.5, passed=True,
                    verdicts=[{"escalated": str(e)}],
                    notes=f"Judge failed (escalate_to_human): {e}",
                )

        axes = d.get("axes", [])
        overall = float(d.get("overall", 0.0))
        passed  = bool(d.get("passed", overall >= 0.7))

        return VerificationOutcome(
            task_id=task.id, rubric_id=rubric_id,
            score=overall, passed=passed,
            verdicts=axes,
            notes="",
        )
