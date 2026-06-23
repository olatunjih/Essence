"""
AuditLogger — append-only audit log with SHA-256 hash chaining.

Each entry's hash covers its full content including the previous entry's hash,
making retrospective modification detectable. Backed by SQLite in a dedicated
audit.db separate from the capsule store.

This replaces the G10 list-append in GuardrailLayer with a tamper-evident log.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.security.audit_logger")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    entry_id      TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    resource      TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    details       TEXT,
    timestamp     REAL NOT NULL,
    previous_hash TEXT NOT NULL,
    entry_hash    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(timestamp);
"""


class AuditLogger:
    """
    Append-only audit log with HMAC-SHA-256 hash chaining.

    Each entry's hash covers its full content including the previous entry's
    hash, keyed by ``Essence_AUDIT_HMAC_KEY`` (env var).  Without the key,
    forging a valid chain is computationally infeasible.

    Falls back to plain SHA-256 (no key) when the env var is absent so the
    system degrades gracefully in development / test environments.

    The log is backed by a separate audit.db to prevent cross-contamination
    with the capsule store.
    """

    _ENV_KEY = "Essence_AUDIT_HMAC_KEY"

    def __init__(self, db_path: Path | None = None) -> None:
        # Load HMAC key; may be empty in dev/test environments
        raw_key = os.environ.get(self._ENV_KEY, "").encode()
        self._hmac_key: bytes = raw_key

        # Derive genesis hash from the key (or all-zeros fallback)
        if self._hmac_key:
            self.GENESIS_HASH = hmac.new(
                self._hmac_key, b"genesis", hashlib.sha256
            ).hexdigest()
        else:
            self.GENESIS_HASH = "0" * 64

        if db_path is None:
            self._db_path: str | None = None
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path = str(db_path)
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)

        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._chain_hash: str = self._load_last_hash()
        self._lock = __import__("threading").Lock()

    def _load_last_hash(self) -> str:
        """Load the hash of the last entry to continue the chain."""
        try:
            row = self._conn.execute(
                "SELECT entry_hash FROM audit_log "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else self.GENESIS_HASH
        except Exception:
            return self.GENESIS_HASH

    def _compute_hash(self, entry: dict) -> str:
        """HMAC-SHA-256 (or plain SHA-256 fallback) of canonical JSON."""
        canonical = json.dumps(entry, sort_keys=True, ensure_ascii=True).encode("utf-8")
        if self._hmac_key:
            return hmac.new(self._hmac_key, canonical, hashlib.sha256).hexdigest()
        return hashlib.sha256(canonical).hexdigest()

    def log(self,
            event_type: str,
            actor: str,
            action: str,
            resource: str,
            outcome: str,
            details: dict | None = None) -> str:
        """
        Append a hash-chained audit entry.
        Returns the entry's hash.
        """
        with self._lock:
            entry_id = str(uuid.uuid4())
            ts       = time.time()
            prev     = self._chain_hash

            entry = {
                "entry_id":      entry_id,
                "event_type":    event_type,
                "actor":         actor,
                "action":        action,
                "resource":      resource,
                "outcome":       outcome,
                "details":       details or {},
                "timestamp":     ts,
                "previous_hash": prev,
            }
            entry_hash = self._compute_hash(entry)

            try:
                self._conn.execute(
                    "INSERT INTO audit_log VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        entry_id, event_type, actor, action,
                        resource, outcome,
                        json.dumps(details or {}),
                        ts, prev, entry_hash,
                    ),
                )
                self._conn.commit()
                self._chain_hash = entry_hash
            except Exception as exc:
                log.warning("audit_log_write_error",
                            extra={"error": str(exc)[:120]})

            log.debug("audit_logged",
                      extra={"event_type": event_type, "actor": actor,
                             "action": action, "hash": entry_hash[:16]})
            return entry_hash

    def verify_chain(self) -> bool:
        """
        Recompute all hashes and verify the chain is intact.
        Returns True if the chain is valid, False if tampering is detected.
        """
        try:
            rows = self._conn.execute(
                "SELECT entry_id, event_type, actor, action, resource, "
                "outcome, details, timestamp, previous_hash, entry_hash "
                "FROM audit_log ORDER BY timestamp ASC"
            ).fetchall()

            prev_hash = self.GENESIS_HASH
            for row in rows:
                (entry_id, event_type, actor, action, resource,
                 outcome, details_json, timestamp, previous_hash, stored_hash) = row

                if previous_hash != prev_hash:
                    log.error("audit_chain_broken",
                              extra={"entry_id": entry_id,
                                     "expected_prev": prev_hash[:16],
                                     "stored_prev":   previous_hash[:16]})
                    return False

                entry = {
                    "entry_id":      entry_id,
                    "event_type":    event_type,
                    "actor":         actor,
                    "action":        action,
                    "resource":      resource,
                    "outcome":       outcome,
                    "details":       json.loads(details_json or "{}"),
                    "timestamp":     timestamp,
                    "previous_hash": previous_hash,
                }
                computed = self._compute_hash(entry)
                if computed != stored_hash:
                    log.error("audit_entry_tampered",
                              extra={"entry_id": entry_id})
                    return False

                prev_hash = stored_hash

            return True
        except Exception as exc:
            log.error("audit_verify_error", extra={"error": str(exc)[:120]})
            return False

    def recent(self, limit: int = 50) -> list[dict]:
        """Return the most recent audit entries."""
        try:
            rows = self._conn.execute(
                "SELECT entry_id, event_type, actor, action, resource, "
                "outcome, details, timestamp, previous_hash, entry_hash "
                "FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    "entry_id":      r[0],
                    "event_type":    r[1],
                    "actor":         r[2],
                    "action":        r[3],
                    "resource":      r[4],
                    "outcome":       r[5],
                    "details":       json.loads(r[6] or "{}"),
                    "timestamp":     r[7],
                    "previous_hash": r[8],
                    "entry_hash":    r[9],
                }
                for r in rows
            ]
        except Exception:
            return []
