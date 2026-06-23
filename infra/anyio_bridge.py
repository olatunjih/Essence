""" — unified thread↔async bridge."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# ANYIO ASYNC BRIDGE  — unified thread↔async boundary
# ══════════════════════════════════════════════════════════════════════════════
# Replaces asyncio.run_coroutine_threadsafe() with anyio's portable bridge.
# Eliminates NATSEventBus's second event loop (a known deadlock source).
# Falls back to asyncio.run_coroutine_threadsafe() when anyio not installed.
#
# ENV:  (none — auto-selected when anyio is installed)

try:
    import anyio as _anyio  # type: ignore   # pip install anyio
    _ANYIO = True
except ImportError:
    _anyio = None  # type: ignore
    _ANYIO = False


def run_async_from_thread(coro: Any) -> Any:
    """
    Run an async coroutine from a sync thread, regardless of which
    async backend (asyncio / trio) is running.
    Falls back to asyncio.run_coroutine_threadsafe() when anyio unavailable.
    """
    if _ANYIO:
        try:
            return _anyio.from_thread.run_sync(lambda: None) or                    _anyio.from_thread.run(coro)
        except Exception:
            pass
    # Fallback: find the running loop
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=5.0)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════════
