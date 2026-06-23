""" — Valkey session store."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# VALKEY SESSION STORE  — multi-process session sharing
# ══════════════════════════════════════════════════════════════════════════════
# When Essence_VALKEY_URL is set, sessions are stored in Valkey/Redis so multiple
# essence up worker processes share state behind a load balancer.
# Falls back to the local OrderedDict store (single-process default).
# ENV:  Essence_VALKEY_URL=redis://localhost:6379/0

_VALKEY_URL = os.environ.get("Essence_VALKEY_URL", "")

try:
    import redis as _redis_mod  # type: ignore
    _VALKEY_AVAILABLE = bool(_VALKEY_URL)
except ImportError:
    _redis_mod = None  # type: ignore
    _VALKEY_AVAILABLE = False


class ValkeySessionStore:
    """
    Redis/Valkey-backed session metadata store for multi-process deployments.
    Stores session config as JSON blobs with TTL.
    Agent objects themselves are NOT stored (they contain unpickleable state);
    only serializable config is persisted and agents are reconstructed on cache miss.
    """

    _TTL = int(os.environ.get("Essence_SESSION_TTL", "3600"))

    def __init__(self, url: str) -> None:
        self._client = _redis_mod.from_url(url, decode_responses=True)
        log.info("valkey_session_store_connected", extra={"url": url.split("@")[-1]})

    def get(self, session_id: str) -> dict | None:
        try:
            raw = self._client.get(f"essence:session:{session_id}")
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def set(self, session_id: str, data: dict) -> None:
        try:
            self._client.setex(f"essence:session:{session_id}",
                                self._TTL,
                                json.dumps(data, default=str))
        except Exception:
            pass

    def delete(self, session_id: str) -> None:
        try:
            self._client.delete(f"essence:session:{session_id}")
        except Exception:
            pass

    def active_count(self) -> int:
        try:
            return len(self._client.keys("essence:session:*"))
        except Exception:
            return 0


# ══════════════════════════════════════════════════════════════════════════════
