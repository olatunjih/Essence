
"""PMP scratch namespace with file-backed retention (7-day default)."""
from __future__ import annotations
import json, os, time
from pathlib import Path


class ScratchNamespace:
    """
    File-backed scratch namespace for PMP mutations.
    Writes are retained for `retention_days`; expired files are pruned on write.
    """

    def __init__(self, base_dir: str = "scratch",
                 retention_days: int = 7) -> None:
        self._base      = Path(base_dir)
        self._retention = retention_days * 86400
        self._base.mkdir(parents=True, exist_ok=True)

    def write(self, event_id: str, **payload: object) -> str:
        """Write payload to scratch and return the file path."""
        self._prune()
        fname = self._base / f"pmp_{event_id}.json"
        data  = {"event_id": event_id, "ts": time.time(), **payload}
        fname.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
        return str(fname)

    def read(self, event_id: str) -> dict | None:
        """Read a scratch entry by event_id. Returns None if expired or missing."""
        fname = self._base / f"pmp_{event_id}.json"
        if not fname.exists():
            return None
        data = json.loads(fname.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) > self._retention:
            fname.unlink(missing_ok=True)
            return None
        return data

    def _prune(self) -> None:
        """Remove entries older than retention window."""
        now = time.time()
        for p in self._base.glob("pmp_*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if now - data.get("ts", 0) > self._retention:
                    p.unlink(missing_ok=True)
            except Exception:
                pass
