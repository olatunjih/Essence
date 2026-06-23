
"""
APDE LLM Router — NN-1: every LLM call passes through here with explicit CallClass.
Implements per-class pool selection, seed derivation, governor gate.
"""
from __future__ import annotations
import json, logging
from typing import Any, Callable
from essence.apde_types import CallClass, ResourceGovernorError
from essence.backends.seed_rules import derive_seed, pool_name_for_class

log = logging.getLogger("essence.apde_router")


class APDERouter:
    """
    NN-1 compliant LLM router.
    - Every call carries an explicit CallClass.
    - Pool is selected from the runtime manifest per call class.
    - Seed is derived deterministically.
    - Governor is consulted before every call (NN-1 + NN-5).
    - Unknown call classes are refused.
    - Calls exceeding governor grant are refused.
    - Part 9: input + output pass through PIIRedactor when available.
    """

    def __init__(self, manifest: Any, governor: Any,
                 provider_fn: "Callable | None" = None,
                 epoch_id: str = "") -> None:
        self._manifest   = manifest
        self._governor   = governor
        self._provider   = provider_fn or self._default_provider
        self._epoch_id   = epoch_id or manifest.runtime_id
        # PIIRedactor — injected lazily on first call
        self._pii_redactor: Any = None
        self._pii_loaded = False

    def _get_pii_redactor(self) -> "Any":
        """Lazily load PIIRedactor; returns None when unavailable."""
        if not self._pii_loaded:
            self._pii_loaded = True
            try:
                from essence.security.pii_redactor import PIIRedactor
                self._pii_redactor = PIIRedactor()
            except Exception:
                self._pii_redactor = None
        return self._pii_redactor

    def call(self,
             call_class: CallClass,
             messages: list[dict],
             task_id: str,
             epoch_id: str = "",
             rubric_id: str = "",
             rubric_version: str = "",
             base_plan_hash: str = "",
             mutation_event_id: str = "",
             max_tokens: int = 2048) -> str:
        """
        Issue an LLM call.
        Validates call class, requests governor grant, derives seed, calls provider.
        """
        if not isinstance(call_class, CallClass):
            raise ValueError(
                f"APDERouter: call_class must be a CallClass enum, got {call_class!r}")

        eid = epoch_id or self._epoch_id

        # Governor gate (NN-1, NN-8 step 8)
        try:
            self._governor.grant(call_class, task_id, max_tokens)
        except ResourceGovernorError as e:
            raise ResourceGovernorError(
                f"APDERouter: governor denied {call_class.value} call "
                f"for task {task_id}: {e}") from e

        # Seed derivation (A4, NN-5)
        seed = derive_seed(
            call_class=call_class,
            task_id=task_id,
            epoch_id=eid,
            rubric_id=rubric_id,
            rubric_version=rubric_version,
            base_plan_hash=base_plan_hash,
            mutation_event_id=mutation_event_id,
        )

        # Pool selection from manifest
        pool_name = pool_name_for_class(call_class)
        model     = self._manifest.primary_model(pool_name)
        # Track last used model so APDEVerifier can surface it to the
        # bandit reward callback without a separate round-trip.
        self._last_used_model = model

        log.debug("apde_router_call", extra={
            "call_class": call_class.value,
            "pool": pool_name,
            "model": model,
            "task_id": task_id,
            "seed": seed & 0xFFFF,  # partial seed for log
        })

        # Redact PII from input messages before sending to provider
        pii = self._get_pii_redactor()
        if pii is not None:
            try:
                messages = [
                    {**m, "content": pii.redact(m.get("content", ""))}
                    if m.get("role") == "user" else m
                    for m in messages
                ]
            except Exception:
                pass  # redaction best-effort — never block on failure

        # Pass call_class so provider can branch on PLAN/EXEC/VERIFY (#8)
        raw_output = self._provider(messages=messages, model=model,
                                    max_tokens=max_tokens, seed=seed,
                                    call_class=call_class)

        # Redact PII from provider output before returning
        if pii is not None:
            try:
                raw_output = pii.redact(raw_output)
            except Exception:
                pass

        return raw_output

    @staticmethod
    def _default_provider(messages: list[dict], model: str,
                          max_tokens: int, seed: int,
                          call_class: "CallClass | None" = None) -> str:
        """
        Default provider: returns structured JSON stubs keyed on CallClass for
        offline/test use.  Keyed on call_class rather than prompt substrings so
        verification calls are never misrouted into the intent-compression branch.
        In production a real provider_fn is wired in via boot_kernel().
        """
        cc = getattr(call_class, "value", None) if call_class is not None else None

        if cc == "PLAN":
            last_user = next(
                (m["content"] for m in reversed(messages) if m.get("role") == "user"),
                ""
            )
            if "decompose" in last_user.lower() or '"tasks"' in last_user or "tasks" in last_user:
                import hashlib
                tid = hashlib.sha256(last_user.encode()).hexdigest()[:8]
                return json.dumps([{
                    "id": f"task-{tid}",
                    "goal": "Execute the requested operation",
                    "reads": [], "writes": [], "tools": [],
                    "done_when": "task completed", "risk": "low",
                }])
            return json.dumps({
                "goal": "Complete the stated task",
                "success_signals": ["task completed successfully"],
                "artifacts":       [],
                "budget":          {"tokens": 4096, "usd": 0.01},
                "constraints":     [],
                "out_of_scope":    [],
            })

        if cc == "VERIFY":
            return json.dumps({
                "axes":    [{"name": "correctness", "score": 0.8, "notes": "stub"}],
                "overall": 0.8,
                "passed":  True,
                "notes":   "offline stub",
            })

        # EXEC (and any unknown class)
        return json.dumps({"done": True, "result": "ok", "tool_calls": []})
