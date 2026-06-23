"""
AdapterRegistry — YAML-configurable data adapter system.

Loads adapter definitions from <workspace>/config/adapters.yaml and
instantiates each adapter class. Wraps every adapter call with:
  - Token-bucket rate limiting (per adapter rpm limit)
  - CircuitBreaker (from infra/circuit.py)
  - DataProvenance annotation on every returned dataset

Config schema (adapters.yaml):
  adapters:
    - name: my_adapter
      module: my_package.adapters.my_module
      class: MyAdapter
      signal_type: hard          # hard | soft
      rate_limit: 60             # requests per minute
      credentials:
        api_key: ${MY_API_KEY}
      circuit_breaker:
        failure_threshold: 5
        recovery_timeout_s: 60
"""
from __future__ import annotations

import importlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.adapters.registry")


def _expand_env(value: Any) -> Any:
    """Expand ${ENV_VAR} references in string values."""
    if isinstance(value, str):
        return re.sub(
            r"\$\{([^}]+)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(i) for i in value]
    return value


class _RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, rpm: int) -> None:
        self._rpm       = max(1, rpm)
        self._tokens    = float(self._rpm)
        self._last_refill = time.monotonic()

    def acquire(self) -> bool:
        now  = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            float(self._rpm),
            self._tokens + elapsed * (self._rpm / 60.0),
        )
        self._last_refill = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class _CircuitBreaker:
    """Simple circuit breaker (closed → open → half-open)."""

    def __init__(self, failure_threshold: int = 5,
                 recovery_timeout_s: float = 60.0) -> None:
        self._threshold  = failure_threshold
        self._timeout    = recovery_timeout_s
        self._failures   = 0
        self._state      = "closed"   # closed | open | half-open
        self._opened_at  = 0.0

    def is_open(self) -> bool:
        if self._state == "open":
            if time.monotonic() - self._opened_at >= self._timeout:
                self._state = "half-open"
                return False
            return True
        return False

    def on_success(self) -> None:
        self._failures = 0
        self._state    = "closed"

    def on_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._state    = "open"
            self._opened_at = time.monotonic()
            log.warning("circuit_breaker_opened",
                        extra={"failures": self._failures})


class _WrappedAdapter:
    """Adapter instance wrapped with rate limiter and circuit breaker."""

    def __init__(self, name: str, instance: Any,
                 signal_type: str,
                 rate_limiter: _RateLimiter,
                 circuit_breaker: _CircuitBreaker) -> None:
        self.name            = name
        self._instance       = instance
        self._signal_type    = signal_type
        self._rate_limiter   = rate_limiter
        self._circuit        = circuit_breaker

    async def call(self, **params: Any) -> dict:
        """Call the underlying adapter with circuit breaker and rate limiting."""
        from essence.pipelines.provenance import DataProvenance, SignalType

        if self._circuit.is_open():
            raise RuntimeError(
                f"Adapter '{self.name}' circuit breaker is OPEN — "
                "too many recent failures."
            )
        if not self._rate_limiter.acquire():
            raise RuntimeError(
                f"Adapter '{self.name}' rate limit exceeded. "
                f"Max: {self._rate_limiter._rpm} rpm"
            )

        t_start = time.monotonic()
        try:
            result = await self._instance.fetch(**params)
            self._circuit.on_success()
            elapsed_ms = (time.monotonic() - t_start) * 1000

            provenance = DataProvenance(
                signal_type=SignalType(self._signal_type),
                source=self.name,
                fetched_at=time.time(),
                validation_status="passed",
                record_count=len(result) if isinstance(result, list) else 1,
            )
            return {"data": result, "provenance": provenance.to_dict(),
                    "elapsed_ms": round(elapsed_ms, 2)}
        except Exception as exc:
            self._circuit.on_failure()
            log.warning("adapter_call_failed",
                        extra={"adapter": self.name, "error": str(exc)[:120]})
            raise


class AdapterRegistry:
    """
    Loads adapter definitions from <workspace>/config/adapters.yaml and
    instantiates each adapter class wrapped with rate limiting and circuit breakers.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, _WrappedAdapter] = {}

    def load(self, workspace: Path) -> None:
        """Load and instantiate all adapters from config/adapters.yaml."""
        config_path = workspace / "config" / "adapters.yaml"
        if not config_path.exists():
            log.debug("adapters_config_missing",
                      extra={"path": str(config_path)})
            return

        try:
            import yaml  # type: ignore
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            log.warning("adapters_config_parse_error",
                        extra={"error": str(exc)[:120]})
            return

        for spec in raw.get("adapters", []):
            try:
                self._load_adapter(spec)
            except Exception as exc:
                log.warning("adapter_load_error",
                            extra={"name": spec.get("name", "?"),
                                   "error": str(exc)[:120]})

    def _load_adapter(self, spec: dict) -> None:
        name        = spec["name"]
        module_path = spec["module"]
        class_name  = spec["class"]
        signal_type = spec.get("signal_type", "hard")
        rpm         = int(spec.get("rate_limit", 60))
        cb_spec     = spec.get("circuit_breaker", {})
        creds       = _expand_env(spec.get("credentials", {}))

        module   = importlib.import_module(module_path)
        cls      = getattr(module, class_name)
        instance = cls(**creds)

        wrapped = _WrappedAdapter(
            name=name,
            instance=instance,
            signal_type=signal_type,
            rate_limiter=_RateLimiter(rpm),
            circuit_breaker=_CircuitBreaker(
                failure_threshold=int(cb_spec.get("failure_threshold", 5)),
                recovery_timeout_s=float(cb_spec.get("recovery_timeout_s", 60.0)),
            ),
        )
        self._adapters[name] = wrapped
        log.info("adapter_loaded", extra={"name": name, "signal_type": signal_type})

    def get(self, name: str) -> _WrappedAdapter:
        """Return a wrapped adapter by its configured name."""
        adapter = self._adapters.get(name)
        if adapter is None:
            raise KeyError(
                f"Adapter '{name}' not found. "
                f"Registered: {list(self._adapters.keys())}"
            )
        return adapter

    async def call(self, adapter_name: str, **params: Any) -> dict:
        """
        Call adapter_name with params, wrapped in circuit breaker and rate limiter.
        Returns {'data': ..., 'provenance': DataProvenance(...)}
        """
        adapter = self.get(adapter_name)
        return await adapter.call(**params)

    def list_adapters(self) -> list[dict]:
        return [
            {
                "name":         a.name,
                "signal_type":  a._signal_type,
                "circuit_open": a._circuit.is_open(),
            }
            for a in self._adapters.values()
        ]
