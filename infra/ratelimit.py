""" rate limiter +  Valkey-backed."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.valkey import _VALKEY_AVAILABLE, _VALKEY_URL  # noqa: F401  [real source bug]

# REQUEST RATE LIMITER  — per-(user, route) token-bucket
# ══════════════════════════════════════════════════════════════════════════════
# Configurable via config.toml [rate_limits] or env:
#   Essence_RL_CHAT=20/minute   Essence_RL_SHELL=10/minute  (0 = disabled)
# Used as FastAPI Depends() — returns 429 with Retry-After on breach.
# Thread-safe in-process; for multi-process use Valkey () backend.

import collections as _coll

_RL_DEFAULTS = {
    "chat":     int(os.environ.get("Essence_RL_CHAT",  "60")),   # per minute
    "shell":    int(os.environ.get("Essence_RL_SHELL", "20")),
    "agent":    int(os.environ.get("Essence_RL_AGENT", "10")),
    "admin":    int(os.environ.get("Essence_RL_ADMIN", "30")),
}


class RateLimiter:
    """
    Sliding-window token-bucket rate limiter.
    Keyed by (user_id, route).  Window = 60 seconds.
    Thread-safe; per-process only (no cross-worker state).
    """
    _WINDOW = 60.0



    _SWEEP_EVERY = 1000   # sweep expired keys every N check() calls

    def __init__(self) -> None:
        self._windows: dict[str, _coll.deque] = {}
        self._lock  = threading.Lock()
        self._calls = 0

    def _sweep(self) -> None:
        """Remove fully-expired window entries to bound memory growth."""
        cutoff = time.monotonic() - self._WINDOW
        dead   = [k for k, dq in self._windows.items()
                  if not dq or dq[-1] < cutoff]
        for k in dead:
            self._windows.pop(k, None)

    def check(self, user_id: str, route: str, limit: int | None = None) -> tuple[bool, float]:
        """
        Check if (user_id, route) is within rate limit.
        Returns (allowed: bool, retry_after_seconds: float).
        limit=None → use _RL_DEFAULTS[route] or 60.
        """
        if limit is None:
            # v23: Read from EssenceConfig at call time so config changes propagate
            # without restart. _RL_DEFAULTS is a static fallback only.
            try:
                _cfg = get_config()
                limit = getattr(_cfg, f"rl_{route}", None) or _RL_DEFAULTS.get(route, 60)
            except Exception:
                limit = _RL_DEFAULTS.get(route, 60)
        if limit <= 0:
            return True, 0.0

        key = f"{user_id}:{route}"
        now = time.monotonic()
        cutoff = now - self._WINDOW

        with self._lock:
            self._calls += 1
            if self._calls % self._SWEEP_EVERY == 0:
                self._sweep()
            dq = self._windows.setdefault(key, _coll.deque())
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= limit:
                retry_after = self._WINDOW - (now - dq[0])
                return False, max(0.0, retry_after)
            dq.append(now)
            return True, 0.0

    def reset(self, user_id: str, route: str) -> None:
        key = f"{user_id}:{route}"
        with self._lock:
            self._windows.pop(key, None)


RATE_LIMITER = RateLimiter()


# ══════════════════════════════════════════════════════════════════════════════

# VALKEY-BACKED RATE LIMITING  — multi-process safe
# ══════════════════════════════════════════════════════════════════════════════
# When Essence_VALKEY_URL is set, RateLimiter delegates to Valkey via
# atomic INCR + EXPIRE so all worker processes share a single limit.
# Falls back to in-process sliding-window when Valkey is not configured.
#
# Valkey key format:  essence:rl:{user_id}:{route}   TTL = 60s

class ValkeyRateLimiter:
    """
    Atomic Valkey-backed rate limiter for multi-process deployments.
    Uses Redis INCR + EXPIRE — O(1) per check, no Lua scripts needed.
    """

    _WINDOW = 60   # seconds

    def __init__(self, redis_client: Any) -> None:
        self._r = redis_client

    def check(self, user_id: str, route: str,
               limit: int | None = None) -> tuple[bool, float]:
        """Returns (allowed, retry_after)."""
        if limit is None:
            try:
                _cfg = get_config()
                limit = getattr(_cfg, f"rl_{route}", None) or _RL_DEFAULTS.get(route, 60)
            except Exception:
                limit = _RL_DEFAULTS.get(route, 60)
        if limit <= 0:
            return True, 0.0
        key = f"essence:rl:{user_id}:{route}"
        try:
            # v26: Anchor window to first request (INCR then EXPIRE only when count==1)
            # INCR + EXPIRE every time was sliding the window forward on each request.
            pipe  = self._r.pipeline()
            pipe.incr(key)
            count = pipe.execute()[0]
            if count == 1:
                self._r.expire(key, self._WINDOW)   # anchor TTL to first request
            if count > limit:
                ttl = max(0, self._r.ttl(key))
                return False, float(ttl)
            return True, 0.0
        except Exception:
            return True, 0.0   # Valkey down → fail-open

    def reset(self, user_id: str, route: str) -> None:
        key = f"essence:rl:{user_id}:{route}"
        try:
            self._r.delete(key)
        except Exception:
            pass


_valkey_rate_limiter: "ValkeyRateLimiter | None" = None
_valkey_rl_lock = threading.Lock()


def get_rate_limiter() -> Any:
    """
    Return a singleton ValkeyRateLimiter when Essence_VALKEY_URL is set.
    v26: Creates the connection ONCE and caches it — previous version created
    a new Redis connection pool on every call, leaking connections under load.
    Falls back to the in-process RATE_LIMITER when Valkey is not configured.
    """
    global _valkey_rate_limiter
    if _VALKEY_AVAILABLE and _VALKEY_URL:
        with _valkey_rl_lock:
            if _valkey_rate_limiter is None:
                try:
                    r = _redis_mod.from_url(_VALKEY_URL, decode_responses=True)
                    _valkey_rate_limiter = ValkeyRateLimiter(r)
                except Exception:
                    pass
        if _valkey_rate_limiter is not None:
            return _valkey_rate_limiter
    return RATE_LIMITER


# ══════════════════════════════════════════════════════════════════════════════
