
"""
# Stage B — Hierarchical Decomposition.
Decomposes an IntentCapsule into a PlanDAG of Tasks.
Enforces Axioms A5 (coverage) and A6 (disjointness).

Fix 10: TreeOfThought is now wired into Decomposer as an optional parameter.
When provided and the task complexity score exceeds _TOT_THRESHOLD, decompose()
expands N candidate plans, scores them, and selects the best.  All LLM calls
go through APDERouter so ResourceGovernor, guardrails, and cost tracking apply
normally.  When tot=None (default), single-plan decomposition is used.
"""
from __future__ import annotations
import json, uuid
from typing import Any
from essence.apde_types import (
    CallClass, IntentCapsule, Task, PlanDAG, TaskState, RiskLevel,
    APDE_NAMESPACE, AxiomViolation,
)
from essence.agents.planning.coverage import covers
from essence.agents.planning.disjointness import check_disjointness


class Decomposer:
    """
    Hierarchical task decomposer.

    Returns a single-task plan if is_SUU() is True (goal is short and has at
    most one success signal).  Otherwise calls the LLM router (PLAN call class)
    to produce a JSON task list that is parsed into a PlanDAG.

    Fix 10: when a TreeOfThought instance is provided and the capsule complexity
    score (heuristic based on signal count and goal length) exceeds
    _TOT_THRESHOLD, N candidate plans are expanded, scored, and the
    best-scoring plan is used.  The _TOT_ENABLED env flag in tot.py is
    superseded by this parameter — pass tot=None to disable.
    """

    _SYS = (
        "You are a task decomposer. Given a goal and constraints, produce a JSON "
        "array of atomic tasks. Each task: {id, goal, reads: [], writes: [], "
        "tools: [], done_when: string, risk: low|medium|high}. "
        "Tasks must collectively cover all success_signals. "
        "Writes must not overlap between tasks. "
        "Respond ONLY with a valid JSON array. No markdown."
    )

    _TOT_THRESHOLD = 0.7  # complexity score above which ToT is triggered

    def __init__(self, llm_router: Any, epoch_id: str,
                 max_depth: int = 4,
                 tot: "Any | None" = None) -> None:
        """
        Args:
            llm_router: APDERouter instance for LLM calls.
            epoch_id:   Current epoch identifier from RuntimeManifest.
            max_depth:  Maximum recursion depth for hierarchical decomposition.
            tot:        Optional TreeOfThought instance.  When provided
                        and the capsule complexity score exceeds _TOT_THRESHOLD,
                        multi-branch planning is used.  When None, single-plan
                        decomposition is always used.
        """
        self._router    = llm_router
        self._epoch_id  = epoch_id
        self._max_depth = max_depth
        self._tot       = tot

    @staticmethod
    def is_SUU(capsule: IntentCapsule) -> bool:
        """
        Sufficiently Understandable Unit check.

        A capsule is SUU if it has at most one success signal and its goal is
        short enough for direct execution (≤20 words).  SUU capsules are
        wrapped in a single-task plan without calling the LLM.
        """
        return (
            len(capsule.success_signals) <= 1
            and len(capsule.goal.split()) <= 20
        )

    @staticmethod
    def _complexity_score(capsule: IntentCapsule) -> float:
        """
        Compute a heuristic complexity score in [0, 1] for a capsule.

        Fix 10: this score gates ToT expansion.  Factors:
          - Number of success signals (more → higher complexity)
          - Goal word count (longer → higher complexity)
          - Number of artifacts (more → higher complexity)

        The formula is deliberately simple; a learning-engine-calibrated scorer
        can replace it in the future without changing the interface.
        """
        signal_score   = min(1.0, len(capsule.success_signals) / 5.0)
        goal_score     = min(1.0, len(capsule.goal.split()) / 30.0)
        artifact_score = min(1.0, len(capsule.artifacts) / 4.0)
        return (signal_score * 0.5) + (goal_score * 0.3) + (artifact_score * 0.2)

    def decompose(self, capsule: IntentCapsule,
                  plan_id: str = "",
                  runtime_manifest_id: str = "") -> PlanDAG:
        """
        Decompose capsule into a PlanDAG.

        If is_SUU(), wraps in single-task plan and freezes immediately.
        Otherwise calls the LLM for hierarchical decomposition.
        If ToT is wired and complexity ≥ _TOT_THRESHOLD, expands N branches
        and selects the best-scoring one.
        Validates coverage (A5) and disjointness (A6) on the resulting tasks.

        Args:
            capsule:               The IntentCapsule to decompose.
            plan_id:               Optional explicit plan ID (pass "" for auto).
            runtime_manifest_id:   Runtime manifest identifier.

        Returns:
            A frozen PlanDAG ready for ratification and execution.

        Raises:
            AxiomViolation: if the LLM returns invalid JSON, an empty task list,
                            or tasks that violate A5 (coverage) or A6 (disjointness).
        """
        # Plan IDs are uuid4-generated (not derived from capsule.id).
        # Kernel.tick() and user_input() look up plans via
        # plan_repo.get_active_plan(capsule_id) instead of reconstructing the ID.
        pid = plan_id or str(uuid.uuid4())

        if self.is_SUU(capsule):
            root_task = Task(
                id=str(uuid.uuid4()),
                capsule_id=capsule.id,
                goal=capsule.goal,
                reads=list(capsule.constraints),
                writes=list(capsule.artifacts),
                tools=[],
                done_when=" and ".join(capsule.success_signals) if capsule.success_signals else "task completed",
                risk=RiskLevel.LOW,
            )
            plan = PlanDAG(
                id=pid,
                capsule_id=capsule.id,
                tasks=[root_task],
                runtime_manifest_id=runtime_manifest_id,
            )
            plan.freeze()
            return plan

        # Use Tree-of-Thought for high-complexity capsules
        complexity = self._complexity_score(capsule)
        if self._tot is not None and complexity >= self._TOT_THRESHOLD:
            plan = self._tot_decompose(capsule, pid, runtime_manifest_id, complexity)
        else:
            plan = self._single_decompose(capsule, pid, runtime_manifest_id)

        return plan

    def _single_decompose(self, capsule: IntentCapsule, pid: str,
                          runtime_manifest_id: str) -> PlanDAG:
        """
        Standard single-branch LLM decomposition.

        Calls the LLM once, parses the JSON task array, validates A5 and A6,
        and returns a frozen PlanDAG.
        """
        prompt = json.dumps({
            "goal":            capsule.goal,
            "success_signals": capsule.success_signals,
            "artifacts":       capsule.artifacts,
            "constraints":     capsule.constraints,
            "out_of_scope":    capsule.out_of_scope,
        })

        response = self._router.call(
            call_class=CallClass.PLAN,
            messages=[
                {"role": "system", "content": self._SYS},
                {"role": "user",   "content": prompt},
            ],
            task_id=capsule.id,
            epoch_id=self._epoch_id,
        )

        try:
            task_dicts = json.loads(response)
        except json.JSONDecodeError as e:
            raise AxiomViolation(
                f"Decomposer: LLM did not return valid JSON: {e}") from e

        return self._build_plan_from_task_dicts(task_dicts, capsule, pid, runtime_manifest_id)

    def _tot_decompose(self, capsule: IntentCapsule, pid: str,
                       runtime_manifest_id: str,
                       complexity: float) -> PlanDAG:
        """
        Tree-of-Thought multi-branch decomposition.

        Expands N candidate plans via self._tot.expand(), scores them, and
        returns the best-scoring plan as a PlanDAG.  Falls back to
        _single_decompose() if ToT returns no usable branches.

        The Decomposer's own _SYS prompt is used (not the agent.py system
        prompt) so that the task-decomposition format requirement is preserved
        across all branches.
        """
        try:
            branches = self._tot.expand(
                goal=capsule.goal,
                context=capsule.raw_prompt,
                plan_sys=self._SYS,
            )
            if not branches:
                return self._single_decompose(capsule, pid, runtime_manifest_id)

            # Score branches if scores are not already set
            if all(b.score == 0.0 for b in branches):
                branches = self._tot.score(branches, capsule.goal)

            best = max(branches, key=lambda b: b.score)
            task_dicts = best.plan if isinstance(best.plan, list) else []

            if not task_dicts:
                return self._single_decompose(capsule, pid, runtime_manifest_id)

            return self._build_plan_from_task_dicts(
                task_dicts, capsule, pid, runtime_manifest_id)

        except Exception:
            # ToT expansion failed — fall back to single decomposition
            return self._single_decompose(capsule, pid, runtime_manifest_id)

    def _build_plan_from_task_dicts(self, task_dicts: list[dict],
                                    capsule: IntentCapsule,
                                    pid: str,
                                    runtime_manifest_id: str) -> PlanDAG:
        """
        Convert a list of task dicts (from LLM output or ToT branch) into a
        frozen PlanDAG after validating Axioms A5 and A6.

        Args:
            task_dicts:          Parsed list of task dictionaries.
            capsule:             The originating IntentCapsule.
            pid:                 Plan ID to assign to the resulting PlanDAG.
            runtime_manifest_id: Runtime manifest identifier.

        Raises:
            AxiomViolation: if the task list is empty, does not cover all
                            success_signals (A5), or has overlapping writes (A6).
        """
        tasks: list[Task] = []
        for i, d in enumerate(task_dicts):
            risk_str = d.get("risk", "low").upper()
            try:
                risk = RiskLevel[risk_str]
            except KeyError:
                risk = RiskLevel.LOW
            t = Task(
                id=d.get("id") or str(uuid.uuid4()),
                capsule_id=capsule.id,
                goal=d.get("goal", f"Task {i}"),
                reads=d.get("reads", []),
                writes=d.get("writes", []),
                tools=d.get("tools", []),
                done_when=d.get("done_when", "task completed"),
                risk=risk,
            )
            tasks.append(t)

        if not tasks:
            raise AxiomViolation("Decomposer: LLM returned empty task list")

        # Axiom A5: coverage check — tasks must address all success_signals
        if not covers(tasks, capsule.success_signals):
            raise AxiomViolation(
                "Axiom A5 violated: tasks do not cover all success_signals")

        # Axiom A6: disjointness — no two tasks may write to the same artifact
        check_disjointness(tasks)

        plan = PlanDAG(
            id=pid,
            capsule_id=capsule.id,
            tasks=tasks,
            runtime_manifest_id=runtime_manifest_id,
        )
        plan.freeze()
        return plan

    def replan(self, plan: PlanDAG, reason: str,
               capsule: IntentCapsule) -> PlanDAG:
        """
        Replan: decompose again with the failure reason as additional context.

        A new plan ID (uuid4) is generated so the new plan is stored alongside
        the failed one rather than replacing it (supports audit trail).

        Args:
            plan:    The plan that failed or was superseded.
            reason:  Human-readable reason for replanning (logged in capsule context).
            capsule: The original IntentCapsule.

        Returns:
            A new frozen PlanDAG for the same capsule.
        """
        enriched = IntentCapsule(
            id=capsule.id,
            raw_prompt=capsule.raw_prompt + f"\nReplan reason: {reason}",
            goal=capsule.goal,
            success_signals=capsule.success_signals,
            artifacts=capsule.artifacts,
            budget=capsule.budget,
            constraints=capsule.constraints,
            out_of_scope=capsule.out_of_scope,
            lifecycle_state=capsule.lifecycle_state,
            runtime_manifest_id=capsule.runtime_manifest_id,
            created_at=capsule.created_at,
        )
        # Use a new plan_id so replanned plan is independent of the failed one
        return self.decompose(
            enriched,
            plan_id="",  # force new uuid4
            runtime_manifest_id=plan.runtime_manifest_id,
        )
