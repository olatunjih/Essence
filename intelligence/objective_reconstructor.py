"""ObjectiveReconstructor — separates literal requests from underlying objectives.

Two-pass approach:
  Pass 1 — Heuristic: pattern-match against known request→objective pairs.
            Covers ~70% of common cases without an LLM call.
  Pass 2 — LLM: when heuristic confidence < threshold, call the LLM with
            [TWIN context] + [TEMPORAL goals] + [literal request] and ask
            for the underlying objective.

Example:
  Literal : "Work through the night"
  Objective: "Complete current project deliverable"

  Literal : "Delete all logs"
  Objective: "Free up disk space"
"""
from __future__ import annotations
import dataclasses
import logging
import re
from typing import Any

log = logging.getLogger("essence.intelligence.objective_reconstructor")

_HEURISTIC_CONFIDENCE_THRESHOLD = 0.65
_LLM_MAX_TOKENS = 256


@dataclasses.dataclass
class ReconstructionResult:
    literal_request:        str
    inferred_objective:     str
    alternative_paths:      list[str]
    confidence:             float
    requires_clarification: bool
    source:                 str = "heuristic"    # "heuristic" | "llm" | "passthrough"


_HEURISTIC_PATTERNS: list[tuple[str, str, list[str]]] = [
    (
        r"work(ing)?\s+(through|all)\s+(the\s+)?night",
        "Complete current project deliverable",
        ["Break the project into smaller milestones",
         "Delegate non-critical tasks", "Set a hard stop time"],
    ),
    (
        r"delete\s+(all|every(thing)?)",
        "Free up resource space and reduce clutter",
        ["Archive instead of delete", "Identify and remove only stale items",
         "Compress and rotate"],
    ),
    (
        r"just\s+(do|run|execute|start)\s+it",
        "Achieve the underlying goal as quickly as possible",
        ["Confirm which goal this refers to",
         "Run a dry-run first", "Set a timeout"],
    ),
    (
        r"(send|email|message|notify)\s+(everyone|all|the\s+team)",
        "Communicate a status update broadly",
        ["Target only relevant stakeholders",
         "Draft message for review first"],
    ),
    (
        r"(cancel|stop|kill)\s+(everything|all)",
        "Halt the current operation cleanly",
        ["Graceful shutdown with state save",
         "Pause and resume later", "Cancel only blocking tasks"],
    ),
    (
        r"make\s+it\s+faster",
        "Improve system performance for the user's workload",
        ["Profile first to find bottleneck",
         "Cache hot paths", "Parallelise independent steps"],
    ),
    (
        r"(fix|resolve)\s+(the\s+)?bug",
        "Restore correct behaviour of the affected feature",
        ["Add a regression test after fix",
         "Root-cause analysis before patch"],
    ),
    (
        r"(rewrite|redo|redo\s+from\s+scratch)",
        "Replace the current implementation with a better design",
        ["Incremental refactor to reduce risk",
         "Identify which parts are worth keeping"],
    ),
]


class ObjectiveReconstructor:
    """
    Separates the literal request from the underlying objective.

    Parameters
    ----------
    llm_router : APDERouter
        Router used for Pass 2 (LLM reconstruction).
    twin : PersonalTwin, optional
        Provides user profile context for LLM prompt enrichment.
    temporal : TemporalCognitionPlane, optional
        Provides long-horizon goals for LLM prompt enrichment.
    """

    def __init__(
        self,
        llm_router: Any,
        twin:       Any = None,
        temporal:   Any = None,
    ) -> None:
        self._router   = llm_router
        self._twin     = twin
        self._temporal = temporal

    # ── public API ──────────────────────────────────────────────────────────

    def reconstruct(
        self,
        raw_request: str,
        context: dict | None = None,
    ) -> ReconstructionResult:
        """
        Return a ReconstructionResult for *raw_request*.

        Tries heuristics first; falls back to LLM when confidence is low.
        """
        raw = raw_request.strip()

        # Pass 1 — heuristic
        result = self._heuristic_pass(raw)
        if result.confidence >= _HEURISTIC_CONFIDENCE_THRESHOLD:
            log.debug(
                "objective_reconstructor_heuristic",
                extra={"confidence": result.confidence,
                       "objective":  result.inferred_objective[:60]},
            )
            return result

        # Pass 2 — LLM
        llm_result = self._llm_pass(raw, context or {})
        if llm_result is not None:
            log.debug(
                "objective_reconstructor_llm",
                extra={"objective": llm_result.inferred_objective[:60]},
            )
            return llm_result

        # Passthrough — return literal with low confidence flag
        return ReconstructionResult(
            literal_request        = raw,
            inferred_objective     = raw,
            alternative_paths      = [],
            confidence             = 0.5,
            requires_clarification = True,
            source                 = "passthrough",
        )

    # ── private ──────────────────────────────────────────────────────────────

    def _heuristic_pass(self, raw: str) -> ReconstructionResult:
        lower = raw.lower()
        for pattern, objective, alternatives in _HEURISTIC_PATTERNS:
            if re.search(pattern, lower):
                return ReconstructionResult(
                    literal_request        = raw,
                    inferred_objective     = objective,
                    alternative_paths      = alternatives,
                    confidence             = 0.80,
                    requires_clarification = False,
                    source                 = "heuristic",
                )
        return ReconstructionResult(
            literal_request        = raw,
            inferred_objective     = raw,
            alternative_paths      = [],
            confidence             = 0.0,
            requires_clarification = False,
            source                 = "heuristic",
        )

    def _llm_pass(
        self, raw: str, context: dict
    ) -> ReconstructionResult | None:
        if self._router is None:
            return None
        try:
            ctx_parts: list[str] = []
            if self._twin is not None:
                ctx_parts.append(self._twin.context_block())
            if self._temporal is not None:
                goals = self._temporal.all_goals()
                if goals:
                    hs = " | ".join(
                        f"{g.get('horizon')}:{str(g.get('description',''))[:40]}"
                        for g in goals[:3]
                    )
                    ctx_parts.append(f"[TEMPORAL GOALS] {hs}")

            system_block = "\n".join(ctx_parts) if ctx_parts else ""
            prompt = (
                f"{system_block}\n\n"
                f"Literal request: \"{raw}\"\n\n"
                "Respond with JSON only:\n"
                '{"objective": "<underlying goal>", '
                '"alternatives": ["<path1>", "<path2>"], '
                '"requires_clarification": false}'
            )
            raw_out = self._router.complete(
                messages=[{"role": "user", "content": prompt}],
                model="general",
                max_tokens=_LLM_MAX_TOKENS,
                seed=42,
            )
            import json as _json, re as _re
            # Extract JSON from response
            _m = _re.search(r"\{.*\}", raw_out, _re.DOTALL)
            if not _m:
                return None
            obj = _json.loads(_m.group(0))
            return ReconstructionResult(
                literal_request        = raw,
                inferred_objective     = obj.get("objective", raw)[:200],
                alternative_paths      = obj.get("alternatives", [])[:4],
                confidence             = 0.75,
                requires_clarification = bool(obj.get("requires_clarification", False)),
                source                 = "llm",
            )
        except Exception as exc:
            log.debug("objective_reconstructor_llm_error: %s", exc)
            return None
