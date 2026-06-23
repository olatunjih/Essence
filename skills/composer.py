"""
SkillComposer — build multi-step skill pipelines.

Three pipeline modes:
  • Sequential  — run steps one-by-one, feeding each output as the next input.
  • Parallel    — run independent steps concurrently, merge results.
  • Conditional — branch on a predicate applied to the previous step's result.

All pipeline definitions are plain Python dataclasses (no external DSL).
A composed pipeline is itself a SkillSpec (skill_type=COMPOSITION) so it can
be registered in the SkillRepository and reused.
"""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.skills.models import (
    SkillSpec, SkillResult, SkillType, SkillSource, SkillStatus,
    SkillGuardrails,
)
import concurrent.futures as _cf
import dataclasses        as _dc
import json               as _json
import time               as _time
from typing import Any, Callable

log = logging.getLogger("essence.skills.composer")


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline step definitions
# ══════════════════════════════════════════════════════════════════════════════

@_dc.dataclass
class PipelineStep:
    """One step in a skill pipeline."""
    skill_name: str
    # Optional key extraction: if set, only this key from the previous result
    # dict is forwarded as "input" to this step.
    input_key:  str | None = None
    # Static extra inputs merged with the dynamic ones for this step only.
    extra_input: dict      = _dc.field(default_factory=dict)
    # If True, a failure of this step is non-fatal (pipeline continues).
    optional: bool         = False


@_dc.dataclass
class ConditionalBranch:
    """Branch chosen when predicate(result_text) returns True."""
    predicate:  Callable[[str], bool]
    steps:      list[PipelineStep]
    label:      str = "branch"


# ══════════════════════════════════════════════════════════════════════════════
# SkillComposer
# ══════════════════════════════════════════════════════════════════════════════

class SkillComposer:
    """
    Builds, runs, and persists skill pipelines.

    Parameters
    ----------
    executor   : SkillExecutor — used to run individual steps.
    repository : SkillRepository — used to register composed skills.
    """

    def __init__(self, executor: Any, repository: Any) -> None:
        self._executor = executor
        self._repo     = repository

    # ── Sequential pipeline ───────────────────────────────────────────────────

    def run_sequential(self,
                       steps:      list[PipelineStep],
                       init_input: dict | None = None,
                       *,
                       name: str = "sequential_pipeline") -> list[SkillResult]:
        """
        Run steps in order.  Each step receives the output of the previous step
        merged with its own extra_input dict.

        Returns the full list of SkillResult objects (one per step).
        """
        results: list[SkillResult]  = []
        current_input: dict = dict(init_input or {})

        for step in steps:
            merged = {**current_input, **step.extra_input}
            if step.input_key and "result" in current_input:
                merged["input"] = current_input.get(step.input_key, "")

            result = self._executor.execute(step.skill_name, merged)
            results.append(result)

            if not result.ok and not step.optional:
                log.warning("sequential_pipeline_aborted",
                            extra={"step": step.skill_name, "error": result.error})
                break

            # Feed forward: parse JSON output or use raw text
            try:
                parsed = _json.loads(result.result)
                if isinstance(parsed, dict):
                    current_input = parsed
                else:
                    current_input = {"result": result.result, "input": result.result}
            except Exception:
                current_input = {"result": result.result, "input": result.result}

        return results

    # ── Parallel pipeline ─────────────────────────────────────────────────────

    def run_parallel(self,
                     steps:      list[PipelineStep],
                     shared_input: dict | None = None,
                     *,
                     max_workers: int  = 8,
                     name:        str  = "parallel_pipeline") -> list[SkillResult]:
        """
        Run all steps concurrently.  Each step receives shared_input merged
        with its own extra_input.  Results are returned in the original order.
        """
        shared = dict(shared_input or {})
        results: list[SkillResult | None] = [None] * len(steps)

        def _run(idx_step):
            idx, step = idx_step
            merged = {**shared, **step.extra_input}
            return idx, self._executor.execute(step.skill_name, merged)

        with _cf.ThreadPoolExecutor(max_workers=min(max_workers, len(steps) or 1)) as pool:
            futs = {pool.submit(_run, (i, s)): i for i, s in enumerate(steps)}
            for fut in _cf.as_completed(futs):
                try:
                    idx, res = fut.result()
                    results[idx] = res
                except Exception as exc:
                    idx = futs[fut]
                    results[idx] = SkillResult(
                        skill_name = steps[idx].skill_name,
                        status     = "error",
                        error      = str(exc)[:200],
                    )

        return [r for r in results if r is not None]

    # ── Conditional pipeline ──────────────────────────────────────────────────

    def run_conditional(self,
                        init_input:  dict | None,
                        branches:    list[ConditionalBranch],
                        default_steps: list[PipelineStep] | None = None) -> list[SkillResult]:
        """
        Evaluate branches in order; run the first whose predicate returns True.
        Falls back to default_steps if no branch matches.

        Returns the results of the chosen branch.
        """
        current = dict(init_input or {})
        prior_result = current.get("result", "")

        for branch in branches:
            try:
                if branch.predicate(prior_result):
                    log.debug("conditional_branch_taken",
                              extra={"label": branch.label})
                    return self.run_sequential(branch.steps, current)
            except Exception as exc:
                log.debug("branch_predicate_error",
                          extra={"label": branch.label, "error": str(exc)[:80]})

        if default_steps:
            return self.run_sequential(default_steps, current)
        return []

    # ── Compose → SkillSpec ───────────────────────────────────────────────────

    def define_pipeline(self,
                        name:        str,
                        steps:       list[PipelineStep],
                        description: str  = "",
                        mode:        str  = "sequential",
                        category:    str  = "composition",
                        tags:        list[str] | None = None) -> SkillSpec:
        """
        Declare a pipeline as a named SkillSpec and register it in the
        repository so it can be invoked with executor.execute(name, ...).

        The SkillSpec body encodes the pipeline as a JSON step list.
        """
        step_json = _json.dumps(
            [{"skill_name": s.skill_name,
              "extra_input": s.extra_input,
              "optional":    s.optional} for s in steps],
            indent=2,
        )
        body = (
            f"# Pipeline: {name}\n\n"
            f"Mode: **{mode}**\n\n"
            f"## Steps\n\n```json\n{step_json}\n```\n"
        )
        spec = SkillSpec(
            name        = name,
            description = description or f"Composed {mode} pipeline with {len(steps)} steps.",
            version     = "1.0.0",
            skill_type  = SkillType.COMPOSITION,
            source      = SkillSource.LOCAL,
            status      = SkillStatus.ACTIVE,
            category    = category,
            tags        = (tags or []) + ["pipeline", mode],
            body        = body,
            guardrails  = SkillGuardrails(
                max_execution_time_seconds = 300,
                max_tokens                 = 4096,
                max_retries                = 1,
            ),
        )
        self._repo.register(spec, force=True)
        self._repo.save_skill(spec)
        return spec

    # ── Merge parallel results into a summary ─────────────────────────────────

    @staticmethod
    def merge_results(results: list[SkillResult],
                      separator: str = "\n\n---\n\n") -> str:
        """Concatenate result texts from a parallel run into a single string."""
        parts = []
        for r in results:
            if r.ok:
                parts.append(f"[{r.skill_name}]\n{r.result}")
            else:
                parts.append(f"[{r.skill_name} ERROR]\n{r.error}")
        return separator.join(parts)
