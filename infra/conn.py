""" — async + sync connection pool (httpx)."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# CONNECTION POOL MANAGER  — httpx async + sync pools
# ══════════════════════════════════════════════════════════════════════════════
# Replaces per-call urllib.request.urlopen() with pooled httpx clients.
# Reduces backend call latency by 40-60ms (no TCP handshake per request).
# One AsyncClient per server endpoint; shared sync Client for health probes.
#
# ENV:
#   Essence_HTTP_TIMEOUT=10     Default request timeout in seconds
#   Essence_HTTP_MAX_CONN=20    Max connections per host in pool

_HTTP_TIMEOUT  = float(os.environ.get("Essence_HTTP_TIMEOUT", "10"))
_HTTP_MAX_CONN = int(os.environ.get("Essence_HTTP_MAX_CONN", "20"))

try:
    import httpx as _httpx_pool  # type: ignore
    _HTTPX_POOL = True
except ImportError:
    _httpx_pool = None  # type: ignore
    _HTTPX_POOL = False

# Module-level client instances (created lazily, shared across calls)
_sync_http_client:  Any = None
_async_http_client: Any = None
_http_client_lock = threading.Lock()


def get_sync_client() -> Any:
    """Return the shared httpx.Client (sync). Falls back to urllib sentinel."""
    global _sync_http_client
    if not _HTTPX_POOL:
        return None
    with _http_client_lock:
        if _sync_http_client is None or _sync_http_client.is_closed:
            _sync_http_client = _httpx_pool.Client(
                timeout=_HTTP_TIMEOUT,
                limits=_httpx_pool.Limits(
                    max_connections=_HTTP_MAX_CONN,
                    max_keepalive_connections=_HTTP_MAX_CONN // 2),
                follow_redirects=True)
    return _sync_http_client


def get_async_client() -> Any:
    """Return the shared httpx.AsyncClient (async). Falls back to None."""
    global _async_http_client
    if not _HTTPX_POOL:
        return None
    # AsyncClient must be created in an async context — return None if not available
    if _async_http_client is None or _async_http_client.is_closed:
        try:
            _async_http_client = _httpx_pool.AsyncClient(
                timeout=_HTTP_TIMEOUT,
                limits=_httpx_pool.Limits(
                    max_connections=_HTTP_MAX_CONN,
                    max_keepalive_connections=_HTTP_MAX_CONN // 2),
                follow_redirects=True)
        except Exception:
            return None
    return _async_http_client


def http_post_json(url: str, payload: dict, timeout: float | None = None) -> Any:
    """
    POST JSON to url. Returns parsed response dict or raises on error.
    Uses httpx connection pool when available, falls back to urllib.
    """
    t = timeout or _HTTP_TIMEOUT
    if _HTTPX_POOL:
        client = get_sync_client()
        if client:
            resp = client.post(url, json=payload, timeout=t)
            resp.raise_for_status()
            return resp.json()
    # urllib fallback
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data,
                                   headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.loads(r.read())


def http_get_json(url: str, timeout: float | None = None) -> Any:
    """GET JSON from url. Uses httpx pool when available, falls back to urllib."""
    t = timeout or _HTTP_TIMEOUT
    if _HTTPX_POOL:
        client = get_sync_client()
        if client:
            resp = client.get(url, timeout=t)
            resp.raise_for_status()
            return resp.json()
    with urllib.request.urlopen(url, timeout=t) as r:
        return json.loads(r.read())


# ══════════════════════════════════════════════════════════════════════════════
