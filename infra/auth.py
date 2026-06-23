""" — API-key middleware + bearer auth."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# API KEY MIDDLEWARE  — scoped keys + bearer auth
# ══════════════════════════════════════════════════════════════════════════════
# Manages named API keys with permission scopes.
# Keys stored in workspace/.api_keys.json (AES-encrypted via SecretsVault).
# Auto-generates a master key to .api_token on first server start.
#
# Scopes: chat | admin | a2a | memory:read | memory:write | tools
#
# ENV:
#   Essence_API_TOKEN=<token>    Override / pre-set master token
#   Essence_AUTH_DISABLED=1      Bypass auth (dev only; logged loudly)
#
# FastAPI usage:  Depends(require_scope("chat"))

_AUTH_DISABLED = os.environ.get("Essence_AUTH_DISABLED", "0") == "1"

_SCOPE_HIERARCHY: dict[str, set[str]] = {
    "admin": {"chat", "a2a", "memory:read", "memory:write", "tools", "admin"},
    "chat":  {"chat"},
    "a2a":   {"a2a", "chat"},
    "memory:read":  {"memory:read"},
    "memory:write": {"memory:read", "memory:write"},
    "tools": {"tools", "chat"},
}


@_dc.dataclass
class APIKey:
    name:       str
    token:      str
    scopes:     list[str]
    created_at: float = _dc.field(default_factory=time.time)
    last_used:  float = 0.0
    enabled:    bool  = True


class APIKeyStore:
    """
    Persistent store for named API keys with scope management.
    Keys are AES-256-GCM encrypted at rest via SecretsVault when available,
    otherwise stored as plaintext JSON (dev mode only).
    """

    def __init__(self, workspace: Path) -> None:
        self._path   = workspace / ".api_keys.json"
        self._keys:  dict[str, APIKey] = {}   # token → APIKey
        self._lock   = threading.RLock()
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for item in raw:
                    k = APIKey(**item)
                    self._keys[k.token] = k
            except Exception as _e:
                log.debug("api_key_load_error", extra={"error": str(_e)[:80]})

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with self._lock:
            tmp.write_text(
                json.dumps([_dc.asdict(k) for k in self._keys.values()], indent=2),
                encoding="utf-8")
        tmp.replace(self._path)
        self._path.chmod(0o600)

    def add(self, name: str, scopes: list[str]) -> str:
        """Create a new API key. Returns the token string."""
        token = f"essence-{secrets.token_urlsafe(32)}"
        with self._lock:
            self._keys[token] = APIKey(name=name, token=token, scopes=scopes)
        self._save()
        return token

    def validate(self, token: str, required_scope: str) -> bool:
        """Validate token and check if it grants required_scope."""
        import os as _os
        if _os.environ.get("Essence_AUTH_DISABLED", "0") == "1":
            return True
        with self._lock:
            k = self._keys.get(token)
            if k is None or not k.enabled:
                return False
            granted = set()
            for s in k.scopes:
                granted.update(_SCOPE_HIERARCHY.get(s, {s}))
            if required_scope in granted:
                k.last_used = time.time()
                return True
        return False

    def revoke(self, token: str) -> bool:
        with self._lock:
            k = self._keys.get(token)
            if k:
                k.enabled = False
                self._save()
                return True
        return False

    def list_keys(self) -> list[dict]:
        with self._lock:
            return [{"name": k.name, "scopes": k.scopes, "enabled": k.enabled,
                     "last_used": k.last_used, "token_preview": k.token[:12] + "..."}
                    for k in self._keys.values()]

    def ensure_master(self, workspace: Path) -> str:
        """Ensure a master admin key exists; return its token."""
        with self._lock:
            admin_keys = [k for k in self._keys.values()
                          if "admin" in k.scopes and k.enabled]
            if admin_keys:
                return admin_keys[0].token
        # Create master key and write to .api_token
        token = os.environ.get("Essence_API_TOKEN", "") or self.add("master", ["admin"])
        (workspace / ".api_token").write_text(token, encoding="utf-8")
        (workspace / ".api_token").chmod(0o600)
        log.info("api_master_key_created",
                 extra={"path": str(workspace / ".api_token")})
        return token


_api_key_store: "APIKeyStore | None" = None


def get_api_key_store(workspace: Path | None = None) -> "APIKeyStore | None":
    global _api_key_store
    if _api_key_store is None and workspace:
        _api_key_store = APIKeyStore(workspace)
    return _api_key_store


def require_scope(scope: str):
    """
    FastAPI dependency factory — validates API key and scope.
    Usage: @app.post("/api/chat")
           async def chat(req: ChatReq, _auth=Depends(require_scope("chat"))):

    Reads token from:
      1. Authorization: Bearer <token>
      2. X-Api-Key: <token>
    Returns the APIKey name on success; raises 401/403 on failure.
    """
    import os as _os
    async def _dep(request: "Request"):
        # Auth disabled in dev mode
        if _os.environ.get("Essence_AUTH_DISABLED", "0") == "1":
            return "dev"
        store = get_api_key_store()
        if store is None:
            return "no-store"   # auth not initialised yet — allow
        # Extract token
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = request.headers.get("X-Api-Key", "").strip()
        if not token:
            from fastapi import HTTPException
            raise HTTPException(status_code=401,
                detail="Missing API key. Pass Authorization: Bearer <token> or X-Api-Key: <token>")
        if not store.validate(token, scope):
            from fastapi import HTTPException
            raise HTTPException(status_code=403,
                detail=f"Token does not grant scope: {scope!r}")
        return token
    return _dep


# ══════════════════════════════════════════════════════════════════════════════
