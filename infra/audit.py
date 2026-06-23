""" — immutable Merkle audit log."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.orjson_shim import _fast_dumps  # noqa: F401  [real source bug]
from essence.infra.context import get_request_context  # noqa: F401  [real source bug: used in append() without import]

# IMMUTABLE AUDIT LOG  — append-only Merkle chain
# ══════════════════════════════════════════════════════════════════════════════
# Every tool call, LLM call, decision, and memory write is appended to
# workspace/logs/audit.jsonl with a SHA-256 chain linking each entry.
# essence audit verify  checks the chain integrity.
# ENV:  Essence_AUDIT=1  Enable (default: off — adds ~1ms per tool call)

_AUDIT_ENABLED = os.environ.get("Essence_AUDIT", "0") == "1"


class AuditLog:
    """
    Append-only JSONL audit log with SHA-256 hash chain.
    Each entry contains: ts, event_type, data, prev_hash, entry_hash.
    The chain makes tampering detectable: verify() checks prev_hash links.
    """

    def __init__(self, workspace: Path) -> None:
        self._path      = workspace / "logs" / "audit.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock      = threading.Lock()
        self._prev_hash = self._last_hash()

    def _last_hash(self) -> str:
        if not self._path.exists():
            return "0" * 64
        try:
            last_line = ""
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last_line = line.strip()
            if last_line:
                return json.loads(last_line).get("entry_hash", "0" * 64)
        except Exception:
            pass
        return "0" * 64

    def append(self, event_type: str, data: dict) -> None:
        import os as _os
        if not (_os.environ.get("Essence_AUDIT", "0") == "1"):
            return
        with self._lock:
            prev = self._last_hash()
            # v24: Embed request context for trace correlation
            ctx = get_request_context()
            entry: dict = {
                "ts":         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "event_type": event_type,
                "user_id":    ctx.get("user_id", "anon"),
                "session_id": ctx.get("session_id", ""),
                "request_id": ctx.get("request_id", ""),
                "data":       data,
                "prev_hash":  prev,
            }
            # v24: Use _fast_dumps in audit hot path
            entry_str  = _fast_dumps(entry, sort_keys=True, default=str)
            entry_hash = hashlib.sha256(entry_str.encode()).hexdigest()
            entry["entry_hash"] = entry_hash
            line = _fast_dumps(entry, default=str) + "\n"
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)
            self._prev_hash = entry_hash

    def verify(self) -> tuple[bool, int, str]:
        """Verify chain integrity. Returns (ok, entries_checked, error_msg)."""
        if not self._path.exists():
            return True, 0, "no log"
        prev = "0" * 64
        count = 0
        try:
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if entry.get("prev_hash") != prev:
                        return False, count, f"chain break at entry {count}"
                    # Re-derive hash without entry_hash field
                    stored_hash = entry.pop("entry_hash", "")
                    computed    = hashlib.sha256(
                        json.dumps(entry, sort_keys=True, default=str).encode()
                    ).hexdigest()
                    if computed != stored_hash:
                        return False, count, f"hash mismatch at entry {count}"
                    prev = stored_hash
                    count += 1
            return True, count, "ok"
        except Exception as _e:
            return False, count, str(_e)


_audit_log: "AuditLog | None" = None


def get_audit_log(workspace: Path | None = None) -> "AuditLog | None":
    global _audit_log
    import os as _os
    if _audit_log is None and workspace:
        if _os.environ.get("Essence_AUDIT", "0") == "1":
            _audit_log = AuditLog(workspace)
    return _audit_log


# ══════════════════════════════════════════════════════════════════════════════
