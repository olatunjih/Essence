""" — per-backend circuit breaker."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# — per-backend circuit breaker (v22/v23 reference in backends/adapters.py).
# [This module was empty — a real content-loss gap from the build_pkg.py split,
#  not just a missing import. Implemented fresh against the call-site contract
#  in backends/adapters.py: CIRCUIT_BREAKERS.get(name) -> breaker with
#  .allow() / .record_success() / .record_failure(), plus .all_status().]

_CB_FAILURE_THRESHOLD = int(os.environ.get("Essence_CB_FAILURE_THRESHOLD", "5"))
_CB_FAILURES = _CB_FAILURE_THRESHOLD  # public alias used by tests/health probes
_CB_RESET_TIMEOUT_S    = float(os.environ.get("Essence_CB_RESET_TIMEOUT_S", "30"))
_CB_HALF_OPEN_MAX      = int(os.environ.get("Essence_CB_HALF_OPEN_MAX", "1"))


class _CBState(_enum.Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Standard CLOSED → OPEN → HALF_OPEN breaker for one backend/provider.

    CLOSED:     requests flow normally; counts consecutive failures.
    OPEN:       requests are short-circuited (allow() == False) until
                reset_timeout has elapsed since the trip.
    HALF_OPEN:  a small number of probe requests are allowed through;
                a failure re-opens, `halfopen_successes` successes close it.
    """

    def __init__(self, name: str,
                 max_failures: int = _CB_FAILURE_THRESHOLD,
                 reset_timeout: float = _CB_RESET_TIMEOUT_S,
                 halfopen_successes: int = 1):
        self.name               = name
        self._max_failures      = max_failures
        self._reset_timeout     = reset_timeout
        self._halfopen_target   = max(1, halfopen_successes)
        self._state             = _CBState.CLOSED
        self._consec_failures   = 0
        self._half_open_success = 0
        self._opened_at         = 0.0
        self._half_open_probes  = 0
        self._lock              = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._state == _CBState.OPEN:
                if time.monotonic() - self._opened_at >= self._reset_timeout:
                    self._state = _CBState.HALF_OPEN
                    self._half_open_probes  = 0
                    self._half_open_success = 0
                else:
                    return False
            if self._state == _CBState.HALF_OPEN:
                if self._half_open_probes >= _CB_HALF_OPEN_MAX:
                    return False
                self._half_open_probes += 1
            return True

    def record_success(self) -> None:
        with self._lock:
            if self._state == _CBState.HALF_OPEN:
                self._half_open_success += 1
                if self._half_open_success < self._halfopen_target:
                    return
            self._consec_failures   = 0
            self._half_open_success = 0
            self._half_open_probes  = 0
            self._state              = _CBState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._consec_failures += 1
            if self._state == _CBState.HALF_OPEN or self._consec_failures >= self._max_failures:
                self._state             = _CBState.OPEN
                self._opened_at         = time.monotonic()
                self._half_open_success = 0

    def status(self) -> dict:
        with self._lock:
            return {
                "name": self.name, "state": self._state.value,
                "failures": self._consec_failures,
                "opened_at": self._opened_at,
            }

    @property
    def _s(self) -> "_CBStateView":
        """Compatibility view exposing `.failures` / `.state` for callers
        that inspect breaker internals directly (e.g. health-monitor
        probes/tests)."""
        return _CBStateView(self)


class _CBStateView:
    __slots__ = ("_cb",)
    def __init__(self, cb: "CircuitBreaker"):
        self._cb = cb
    @property
    def failures(self) -> int:
        return self._cb._consec_failures
    @property
    def state(self) -> "_CBState":
        return self._cb._state


class _CircuitBreakerRegistry:
    """Process-wide registry of CircuitBreaker instances, one per backend name."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> CircuitBreaker:
        with self._lock:
            cb = self._breakers.get(name)
            if cb is None:
                cb = CircuitBreaker(name)
                self._breakers[name] = cb
            return cb

    def all_status(self) -> list[dict]:
        with self._lock:
            return [cb.status() for cb in self._breakers.values()]


CIRCUIT_BREAKERS = _CircuitBreakerRegistry()


