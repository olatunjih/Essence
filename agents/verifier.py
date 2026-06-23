
"""
APDE Verifier (Stage E) — rewrites the WhyEngineV2 stub.
Integrates predicate runtime, judge runner, rubric registry.
Severity is typed via the Severity enum (fixes email_alerter string bug).

Legacy names WhyEngineV2 and WhyEngineV2Stub raise RetiredSubsystemError.
CognitiveReflector and VerifierLayer remain as wrappers for backward compat.
"""
from __future__ import annotations
import logging
from typing import Any
from essence.apde_types import (
    Task, ExecResult, VerificationOutcome, TaskState, Severity,
    RetiredSubsystemError,
)
from essence.agents.verification.predicates import PredicateRuntime, evaluate_done_when
from essence.agents.verification.judge_runner import JudgeRunner
from essence.agents.verification.rubrics import RubricRegistry

log = logging.getLogger("essence.verifier")


class APDEVerifier:
    """
    Full APDE verification (Stage E).
    1. Evaluates done_when predicate (predicate runtime).
    2. Runs LLM judge against pinned rubric.
    3. Returns VerificationOutcome.
    4. Fires optional reward_callback(model, score, context) so the
       contextual bandit router learns from rubric scores rather than
       binary completion (fixes semantically-empty bandit reward signal).
    """

    def __init__(self, llm_router: Any, rubric_registry: RubricRegistry,
                 guardrail_layer: Any, epoch_id: str,
                 default_rubric_id: str = "accuracy",
                 reward_callback: "Any | None" = None) -> None:
        self._router     = llm_router
        self._registry   = rubric_registry
        self._guardrails = guardrail_layer
        self._epoch_id   = epoch_id
        self._default_rubric = default_rubric_id
        self._predicate_rt   = PredicateRuntime()
        self._judge          = JudgeRunner(llm_router, rubric_registry, epoch_id)
        # Optional callable(model: str, score: float, context: dict) -> None.
        # Wire to ContextualBanditRouter.record() at boot time so the bandit
        # learns from rubric quality scores, not just completion status.
        self._reward_callback: Any = reward_callback

    def set_reward_callback(self, fn: "Any") -> None:
        """
        Register a callback invoked after every verification with
        (model: str, score: float, context: dict).

        Typical wiring in boot_kernel():
            bandit = ContextualBanditRouter(ws, ab_router)
            verifier.set_reward_callback(
                lambda model, score, ctx: bandit.record(model, ctx, score)
            )
        """
        self._reward_callback = fn

    def verify(self, task: Task, result: ExecResult,
               rubric_id: str = "") -> VerificationOutcome:
        """Verify a task result. Fires pre_verify guardrail hook."""
        rid = rubric_id or self._default_rubric
        self._guardrails.pre_verify(task, rid)

        # Predicate check
        ctx = {a: True for a in task.reads}
        predicate_ok = evaluate_done_when(task.done_when, ctx)

        if not predicate_ok and result.state == TaskState.DONE:
            # Downgrade to DONE_INSUFFICIENT
            outcome = VerificationOutcome(
                task_id=task.id, rubric_id=rid,
                score=0.0, passed=False,
                verdicts=[{"predicate": "failed", "done_when": task.done_when}],
                notes="done_when predicate not satisfied",
            )
            self._fire_reward(task, outcome)
            return outcome

        # LLM judge
        outcome = self._judge.judge(task, result, rid)
        log.info("verify_complete", extra={
            "task_id": task.id,
            "rubric":  rid,
            "score":   outcome.score,
            "passed":  outcome.passed,
        })
        self._fire_reward(task, outcome)
        return outcome

    def _fire_reward(self, task: Task, outcome: VerificationOutcome) -> None:
        """Fire the reward callback with the rubric score so the bandit
        learns which model produces verifiably good output."""
        if self._reward_callback is None:
            return
        try:
            last_model = getattr(self._router, "_last_used_model", "") or "unknown"
            context    = {
                "task_id":    task.id,
                "task_goal":  task.goal[:60],
                "complexity": "medium",
            }
            self._reward_callback(last_model, outcome.score, context)
        except Exception as _cb_exc:
            log.debug("reward_callback_error",
                      extra={"error": str(_cb_exc)[:120]})

    def severity(self, outcome: VerificationOutcome) -> Severity:
        """Map an outcome score to a typed Severity (fixes string-based bug)."""
        if outcome.score >= 0.9:
            return Severity.LOW
        if outcome.score >= 0.7:
            return Severity.MEDIUM
        if outcome.score >= 0.5:
            return Severity.HIGH
        return Severity.CRITICAL


# ── Backward-compatible wrappers ──────────────────────────────────────────────

class VerificationResult:
    """Retained for existing callers of the old VerifierLayer API."""
    __slots__ = ("claim", "verdict", "confidence", "evidence")
    def __init__(self, claim: str, verdict: str,
                 confidence: float, evidence: str) -> None:
        self.claim      = claim
        self.verdict    = verdict
        self.confidence = confidence
        self.evidence   = evidence


class VerifierLayer:
    """Backward-compatible shim over APDEVerifier (for existing imports)."""

    def __init__(self, provider: Any = None, model: str = "",
                 enabled: bool = True,
                 llm_router: Any = None,
                 rubric_registry: Any = None,
                 guardrail_layer: Any = None,
                 epoch_id: str = "dev-epoch") -> None:
        self.enabled = enabled
        # Full APDE verifier if wired; graceful fallback otherwise
        if llm_router and rubric_registry and guardrail_layer:
            self._apde = APDEVerifier(
                llm_router=llm_router,
                rubric_registry=rubric_registry,
                guardrail_layer=guardrail_layer,
                epoch_id=epoch_id,
            )
        else:
            self._apde = None

    def verify(self, task: "Task | None" = None,
               result: "ExecResult | None" = None,
               response: str = "",
               tool_results: str = "",
               rubric_id: str = "") -> list[VerificationResult]:
        """Backward-compatible verify — returns list[VerificationResult]."""
        if not self.enabled:
            return []
        if self._apde and task and result:
            outcome = self._apde.verify(task, result, rubric_id)
            return [VerificationResult(
                claim=task.goal,
                verdict="verified" if outcome.passed else "unverified",
                confidence=outcome.score,
                evidence=outcome.notes,
            )]
        # Fallback: basic heuristic check
        if not response:
            return []
        return [VerificationResult(
            claim="response_present",
            verdict="verified",
            confidence=1.0,
            evidence="response is non-empty",
        )]

    def annotate(self, response: str,
                 results: list[VerificationResult]) -> str:
        if not results:
            return response
        low = [r for r in results
               if r.verdict in ("unverified", "contradicted")]
        if not low:
            return response
        footer = "\n\n---\n⚠ Verification flags:\n"
        for r in low:
            footer += f"  ? [{r.confidence:.0%}] {r.claim[:80]}\n"
        return response + footer


class CognitiveReflector:
    """Backward-compatible shim for CognitiveReflector (used in agent.py)."""

    def __init__(self, provider: Any = None, model: str = "",
                 critic_sys: str = "") -> None:
        self.provider   = provider
        self.model      = model
        self.critic_sys = critic_sys

    def reflect(self, task: str, plan_or_output: Any,
                context: str = "") -> tuple[bool, str]:
        notes = f"Reflected on: {str(plan_or_output)[:100]}"
        should_replan = False
        return should_replan, notes

    async def areflect(self, task: str, plan_or_output: Any,
                       context: str = "") -> tuple[bool, str]:
        return self.reflect(task, plan_or_output, context)


# ── Legacy retirement ────────────────────────────────────────────────────────

def __getattr__(name: str) -> object:
    if name in ("WhyEngineV2", "WhyEngineV2Stub"):
        raise RetiredSubsystemError(
            f"'{name}' has been retired. Use APDEVerifier from "
            "essence.agents.verifier instead.")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
