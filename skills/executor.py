"""
SkillExecutor — validated, timed, retry-capable skill execution.

Pipeline per invocation:
  1. Resolve SkillSpec from repository (or accept one directly).
  2. Validate input against input_schema (jsonschema or fallback).
  3. Build a skill-scoped prompt and dispatch to the LLM router.
  4. Enforce max_execution_time guardrail via a daemon thread.
  5. Parse / validate output schema if declared.
  6. Retry up to guardrails.max_retries on transient error.
  7. Record telemetry (latency, error_rate) back in the repository.
  8. Return a SkillResult dataclass.
"""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.skills.models import (
    SkillSpec, SkillResult, SkillSource, SkillStatus,
    parse_skill_frontmatter,
)
import json       as _json
import threading  as _threading
import time       as _time
from typing import Any, Callable

log = logging.getLogger("essence.skills.executor")


# ══════════════════════════════════════════════════════════════════════════════
# Input / Output validators
# ══════════════════════════════════════════════════════════════════════════════

def _validate_schema(schema: dict, data: dict) -> tuple[bool, str]:
    """Validate data against a JSON Schema dict. Returns (ok, error_msg)."""
    if not schema:
        return True, ""
    try:
        import jsonschema  # type: ignore
        try:
            jsonschema.validate(instance=data, schema=schema)
            return True, ""
        except jsonschema.ValidationError as ve:
            return False, ve.message[:200]
    except ImportError:
        required = schema.get("required", [])
        missing  = [k for k in required if k not in data]
        if missing:
            return False, f"Missing required fields: {missing}"
        return True, ""
    except Exception as exc:
        return False, str(exc)[:200]


# ══════════════════════════════════════════════════════════════════════════════
# Timeout helper
# ══════════════════════════════════════════════════════════════════════════════

class _TimeboxedCall:
    """Runs a callable in a daemon thread; raises TimeoutError if it exceeds budget."""

    def __init__(self, fn: Callable, timeout_s: float) -> None:
        self._fn        = fn
        self._timeout_s = timeout_s
        self._result    = None
        self._exc: Exception | None = None

    def run(self) -> Any:
        def _target():
            try:
                self._result = self._fn()
            except Exception as exc:
                self._exc = exc

        t = _threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=self._timeout_s)
        if t.is_alive():
            raise TimeoutError(
                f"Skill execution exceeded {self._timeout_s:.0f}s budget."
            )
        if self._exc is not None:
            raise self._exc
        return self._result


# ══════════════════════════════════════════════════════════════════════════════
# SkillExecutor
# ══════════════════════════════════════════════════════════════════════════════

class SkillExecutor:
    """
    Executes a skill by name or spec, honouring all guardrails.

    Parameters
    ----------
    repository : SkillRepository
        Used for skill lookup and telemetry write-back.
    router : any
        LLM router; must expose router.complete(prompt, call_class, max_tokens) -> str.
        If None the executor returns the skill body without LLM augmentation
        (useful for testing / offline use).
    tool_registry : any, optional
        Allows skills declared with skill_type="automation" to call tools directly.
    """

    def __init__(self,
                 repository:    Any,
                 router:        Any | None = None,
                 tool_registry: Any | None = None) -> None:
        self._repo   = repository
        self._router = router
        self._tools  = tool_registry

    # ── Primary entry point ───────────────────────────────────────────────────

    def execute(self,
                skill_name: str,
                input_data: dict | None = None,
                *,
                override_spec: SkillSpec | None = None) -> SkillResult:
        """
        Execute a skill by name.

        Parameters
        ----------
        skill_name    : name registered in the repository.
        input_data    : dict of inputs (validated against input_schema).
        override_spec : bypass the registry lookup (for inline / composed skills).
        """
        t0         = _time.monotonic()
        input_data = input_data or {}

        # ── 1. Resolve spec ───────────────────────────────────────────────────
        spec = override_spec or self._repo.get(skill_name)
        if spec is None:
            return SkillResult(
                skill_name = skill_name,
                status     = "error",
                error      = f"Skill '{skill_name}' not found in repository.",
            )

        if spec.status == SkillStatus.DISABLED:
            return SkillResult(
                skill_name = skill_name,
                status     = "error",
                error      = f"Skill '{skill_name}' is disabled.",
            )

        # ── 2. Input validation ───────────────────────────────────────────────
        in_schema = spec.input_spec.to_schema() if spec.input_spec.properties else {}
        valid, val_err = _validate_schema(in_schema, input_data)
        if not valid:
            return SkillResult(
                skill_name        = skill_name,
                status            = "validation_error",
                error             = val_err,
                validation_passed = False,
                elapsed_ms        = ((_time.monotonic() - t0) * 1000),
            )

        # ── 3. Execute with retry ─────────────────────────────────────────────
        max_retries = spec.guardrails.max_retries
        last_exc:   Exception | None = None
        result_text = ""
        retry_count = 0

        for attempt in range(max_retries + 1):
            try:
                result_text = _TimeboxedCall(
                    fn        = lambda: self._call_router(spec, input_data),
                    timeout_s = spec.guardrails.max_execution_time_seconds,
                ).run()
                break
            except TimeoutError as exc:
                elapsed = (_time.monotonic() - t0) * 1000
                self._repo.record_usage(skill_name, elapsed, ok=False)
                return SkillResult(
                    skill_name  = skill_name,
                    status      = "timeout",
                    error       = str(exc),
                    elapsed_ms  = elapsed,
                    retry_count = attempt,
                    used_router = (self._router is not None),
                )
            except Exception as exc:
                last_exc    = exc
                retry_count = attempt
                if attempt < max_retries:
                    log.debug("skill_execute_retry",
                              extra={"skill": skill_name, "attempt": attempt,
                                     "error": str(exc)[:80]})
                    _time.sleep(0.5 * (attempt + 1))

        elapsed_ms = (_time.monotonic() - t0) * 1000

        if last_exc is not None and not result_text:
            self._repo.record_usage(skill_name, elapsed_ms, ok=False)
            return SkillResult(
                skill_name  = skill_name,
                status      = "error",
                error       = str(last_exc)[:200],
                elapsed_ms  = elapsed_ms,
                retry_count = retry_count,
                used_router = (self._router is not None),
            )

        # ── 4. Output validation (best-effort) ────────────────────────────────
        out_schema = spec.output_spec.to_schema() if spec.output_spec.properties else {}
        if out_schema:
            try:
                parsed_out = _json.loads(result_text)
                _validate_schema(out_schema, parsed_out)
            except Exception:
                pass  # output validation is advisory only

        # ── 5. Telemetry ──────────────────────────────────────────────────────
        self._repo.record_usage(skill_name, elapsed_ms, ok=True)
        self._emit_metric(skill_name, spec.skill_type.value, "success", elapsed_ms / 1000)

        return SkillResult(
            skill_name  = skill_name,
            status      = "success",
            result      = result_text,
            elapsed_ms  = elapsed_ms,
            retry_count = retry_count,
            used_router = (self._router is not None),
        )

    # ── Router dispatch ───────────────────────────────────────────────────────

    def _call_router(self, spec: SkillSpec, input_data: dict) -> str:
        """Build the prompt and invoke the LLM router."""
        _, body = parse_skill_frontmatter(spec.to_skill_md())

        if self._router is not None:
            prompt = (
                f"[SKILL: {spec.name}]\n\n"
                f"{body.strip()}\n\n"
                f"[INPUT]\n{_json.dumps(input_data, indent=2, ensure_ascii=False)}"
            )
            return self._router.complete(
                prompt      = prompt,
                call_class  = "SKILL",
                max_tokens  = spec.guardrails.max_tokens,
            )
        else:
            # No router — return skill body + echoed input for offline use
            return (
                f"[Skill '{spec.name}' loaded offline (no router available)]\n\n"
                f"{body.strip()}\n\n"
                f"Input received:\n{_json.dumps(input_data, indent=2, ensure_ascii=False)}"
            )

    # ── Metric emission ───────────────────────────────────────────────────────

    @staticmethod
    def _emit_metric(skill: str, skill_type: str, status: str,
                     duration_s: float) -> None:
        try:
            from essence.infra.metrics import record_metric_skill
            record_metric_skill(
                skill      = skill,
                status     = status,
                skill_type = skill_type,
                duration_s = duration_s,
            )
        except Exception:
            pass
