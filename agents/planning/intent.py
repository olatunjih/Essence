
"""
Stage A — Intent Compression.
Compress + merge modes. Produces IntentCapsule from raw prompt via LLM router.
"""
from __future__ import annotations
import hashlib, json, time, uuid
from typing import Any
from essence.apde_types import (
    CallClass, IntentCapsule, APDE_NAMESPACE, AxiomViolation,
)


class IntentCompressor:
    """
    Compress a raw user prompt into a structured IntentCapsule (Stage A).
    Calls the LLM router with call class PLAN, pool plan_model.
    """

    _SYS = (
        "You are a precise intent analyzer. Given a user request, extract: "
        "goal (string), success_signals (list of strings), artifacts (list of "
        "strings, may be empty), budget (dict with 'tokens' int and 'usd' float), "
        "constraints (list), out_of_scope (list). "
        "Respond ONLY with valid JSON matching this schema. No markdown."
    )

    def __init__(self, llm_router: Any, epoch_id: str) -> None:
        self._router   = llm_router
        self._epoch_id = epoch_id

    def compress(self, raw_prompt: str, task_id: str = "",
                 runtime_manifest_id: str = "",
                 intent: Any = None) -> IntentCapsule:
        """
        Compress mode: build a fresh IntentCapsule from raw_prompt.
        Validates that goal, success_signals, artifacts, and budget are non-empty.
        """
        tid = task_id or hashlib.sha256(raw_prompt.encode()).hexdigest()[:16]
        response = self._router.call(
            call_class=CallClass.PLAN,
            messages=[
                {"role": "system", "content": self._SYS},
                {"role": "user",   "content": raw_prompt},
            ],
            task_id=tid,
            epoch_id=self._epoch_id,
        )
        try:
            d = json.loads(response)
        except json.JSONDecodeError as e:
            raise AxiomViolation(
                f"IntentCompressor: LLM did not return valid JSON: {e}") from e

        goal            = d.get("goal", "").strip()
        success_signals = d.get("success_signals", [])
        artifacts       = d.get("artifacts", [])
        budget          = d.get("budget", {})

        if not goal:
            raise AxiomViolation("IntentCapsule: goal must be non-empty")
        if not success_signals:
            raise AxiomViolation("IntentCapsule: success_signals must be non-empty")
        if not budget:
            budget = {"tokens": 4096, "usd": 0.10}

        capsule_id = str(uuid.uuid5(
            APDE_NAMESPACE,
            hashlib.sha256(raw_prompt.encode()).hexdigest()
        ))

        capsule = IntentCapsule(
            id=capsule_id,
            raw_prompt=raw_prompt,
            goal=goal,
            success_signals=success_signals,
            artifacts=artifacts or [],
            budget=budget,
            constraints=d.get("constraints", []),
            out_of_scope=d.get("out_of_scope", []),
            lifecycle_state="draft",
            runtime_manifest_id=runtime_manifest_id,
            created_at=time.time(),
        )
        if intent is not None and hasattr(intent, "nuance"):
            capsule.constraints.append(
                f"detail_level:{intent.nuance.detail_level} "
                f"urgency:{intent.nuance.urgency}"
            )
        return capsule

    def merge(self, capsules: list[IntentCapsule]) -> IntentCapsule:
        """
        Merge mode: combine multiple capsules into one IntentCapsule.
        Used when a user issues a follow-up that extends a prior intent.
        """
        if not capsules:
            raise ValueError("merge() requires at least one capsule")
        if len(capsules) == 1:
            return capsules[0]

        combined_prompt = "\n".join(c.raw_prompt for c in capsules)
        all_signals     = list({s for c in capsules for s in c.success_signals})
        all_artifacts   = list({a for c in capsules for a in c.artifacts})
        all_constraints = list({x for c in capsules for x in c.constraints})
        all_out         = list({x for c in capsules for x in c.out_of_scope})
        merged_budget   = {
            "tokens": sum(c.budget.get("tokens", 0) for c in capsules),
            "usd":    sum(c.budget.get("usd", 0.0)  for c in capsules),
        }

        capsule_id = str(uuid.uuid5(
            APDE_NAMESPACE,
            hashlib.sha256(combined_prompt.encode()).hexdigest()
        ))

        return IntentCapsule(
            id=capsule_id,
            raw_prompt=combined_prompt,
            goal=capsules[-1].goal,
            success_signals=all_signals,
            artifacts=all_artifacts,
            budget=merged_budget,
            constraints=all_constraints,
            out_of_scope=all_out,
            lifecycle_state="draft",
            runtime_manifest_id=capsules[-1].runtime_manifest_id,
            created_at=time.time(),
        )
