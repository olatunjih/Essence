""" — concurrent request semaphore."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# CONCURRENT REQUEST LIMITER  — semaphore per route type
# ══════════════════════════════════════════════════════════════════════════════
# Prevents thread pool saturation from concurrent long-running tasks.
# Separate limits for CPU-bound (agent/task) vs I/O-bound (chat) routes.
#
# ENV:
#   Essence_CONC_CHAT=50    Max concurrent chat requests (default: 50)
#   Essence_CONC_AGENT=10   Max concurrent agent/task requests (default: 10)
#   Essence_CONC_TOOLS=20   Max concurrent tool calls (default: 20)

_CONC_CHAT  = int(os.environ.get("Essence_CONC_CHAT",  "50"))
_CONC_AGENT = int(os.environ.get("Essence_CONC_AGENT", "10"))
_CONC_TOOLS = int(os.environ.get("Essence_CONC_TOOLS", "20"))

_semaphores: dict[str, threading.Semaphore] = {}
_sem_lock   = threading.Lock()


def get_semaphore(route: str) -> threading.Semaphore:
    """Return the concurrency semaphore for the given route type."""
    with _sem_lock:
        if route not in _semaphores:
            limit = {"chat": _CONC_CHAT, "agent": _CONC_AGENT,
                     "tools": _CONC_TOOLS}.get(route, 20)
            _semaphores[route] = threading.Semaphore(limit)
        return _semaphores[route]


class ConcurrencyLimiter:
    """
    Context manager that acquires a semaphore slot for the given route.
    Raises RuntimeError immediately if no slot is available (non-blocking).
    """

    def __init__(self, route: str) -> None:
        self._sem   = get_semaphore(route)
        self._route = route

    def __enter__(self) -> "ConcurrencyLimiter":
        acquired = self._sem.acquire(blocking=False)
        if not acquired:
            raise RuntimeError(
                f"[concurrency_limit_reached: {self._route}] "
                "Too many concurrent requests. Retry shortly.")
        return self

    def __exit__(self, *_) -> None:
        self._sem.release()


# ══════════════════════════════════════════════════════════════════════════════
