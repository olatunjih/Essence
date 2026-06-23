""" — request deduplicator (idempotency)."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# — request deduplicator (idempotency).
# [This module was empty — a content-loss gap from the build_pkg.py split.
#  Implemented fresh against tests/test_infra.py's contract: identical
#  (user_id, route, body) calls within a short TTL window return the same
#  cached result instead of re-executing fn; distinct keys execute
#  independently.]

_DEDUP_TTL_S = float(os.environ.get("Essence_DEDUP_TTL_S", "2.0"))


class RequestDeduplicator:
    """Idempotency helper: coalesces identical concurrent/rapid-repeat
    requests (same user + route + body) into a single execution of `fn`."""

    def __init__(self, ttl: float = _DEDUP_TTL_S):
        self._ttl       = ttl
        self._lock       = threading.Lock()
        self._inflight    : dict[str, threading.Lock] = {}
        self._results     : dict[str, tuple[Any, float]] = {}

    @staticmethod
    def _key(user_id: str, route: str, body: Any) -> str:
        try:
            body_s = json.dumps(body, sort_keys=True, default=str)
        except Exception:
            body_s = str(body)
        return hashlib.sha256(f"{user_id}:{route}:{body_s}".encode("utf-8")).hexdigest()

    def execute(self, user_id: str, route: str, body: Any, fn: "Callable[[], Any]") -> Any:
        key = self._key(user_id, route, body)
        now = time.monotonic()
        with self._lock:
            cached = self._results.get(key)
            if cached and now - cached[1] < self._ttl:
                return cached[0]
            key_lock = self._inflight.setdefault(key, threading.Lock())
        with key_lock:
            with self._lock:
                cached = self._results.get(key)
                if cached and now - cached[1] < self._ttl:
                    return cached[0]
            result = fn()
            with self._lock:
                self._results[key] = (result, time.monotonic())
                self._inflight.pop(key, None)
            return result



