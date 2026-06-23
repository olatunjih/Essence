""" — heartbeat sentinel dispatch."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.workspace.heartbeat import (  # noqa: F401  [real source bug: used below without import]
    _retry_flush_handler, _consolidation_job_message, HeartbeatScheduler,
)
from essence.infra.metrics import push_metrics_to_gateway  # noqa: F401

# HEARTBEAT SENTINEL DISPATCH TABLE 
# ══════════════════════════════════════════════════════════════════════════════
# Replaces the hardcoded if/elif chain in _hb_dispatch with a proper registry.
# Each subsystem registers its sentinel prefix and handler at module load time.
# HeartbeatScheduler.SENTINEL_HANDLERS maps prefix → Callable[[Path], str].
# _hb_dispatch iterates the registry; new sentinels need no server changes.

_SENTINEL_HANDLERS: dict[str, "Callable[[Path], str]"] = {}


def register_sentinel(prefix: str, handler: Callable[[Path], str]) -> None:
    """
    Register a HeartbeatScheduler sentinel handler.
    Prefix is matched with message.startswith(prefix).
    Handler receives the Path extracted from the message (after the colon).
    """
    _SENTINEL_HANDLERS[prefix] = handler
    log.debug("sentinel_registered", extra={"prefix": prefix})


def dispatch_sentinel(message: str) -> str | None:
    """
    Attempt to dispatch a heartbeat message via the sentinel registry.
    Returns handler result string if matched, None if no sentinel matched.
    """
    for prefix, handler in _SENTINEL_HANDLERS.items():
        if message.startswith(prefix):
            try:
                ws_path = Path(message.split(":", 1)[1].strip())
                return handler(ws_path)
            except Exception as _e:
                return f"sentinel_error:{prefix}: {_e}"
    return None


# ── Built-in sentinel registrations (at module load time) ────────────────────
def _register_builtin_sentinels() -> None:
    """Register all built-in sentinel handlers. Called once at module init."""
    register_sentinel("_retry_flush",       _retry_flush_handler)
    register_sentinel("_consolidation",     _consolidation_job_message)
    register_sentinel("_prometheus_push",   lambda ws: (
        HeartbeatScheduler.HEARTBEAT_OK + " prometheus_push:"
        + (" ok" if push_metrics_to_gateway() else " skipped")))


# Register built-in sentinels at module init time
_register_builtin_sentinels()


# ══════════════════════════════════════════════════════════════════════════════
