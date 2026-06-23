""" — EpisodicStore: WAL-safe episodic JSONL memory."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.orjson_shim import _fast_dumps, _fast_loads  # noqa: F401  [real source bug]
from essence.memory.search import _episodic_add_fts  # noqa: F401  [real source bug: used below without import]
from essence.memory.search import episodic_search  # noqa: F401  [real source bug: used in search() without import]

# SQLITE WAL EPISODIC MEMORY  — concurrent-write safe
# ══════════════════════════════════════════════════════════════════════════════
# Replaces episodic.jsonl (plain append-only file) with a SQLite WAL table.
# SQLite WAL provides serialized atomic writes: no mid-line corruption from
# concurrent consolidation + active session writes.
# Schema: (id TEXT PK, ts REAL, session_id TEXT, text TEXT, meta_json TEXT)
# Backward-compatible: existing episodic.jsonl entries are migrated on first open.

_EPISODIC_DDL = """
    CREATE TABLE IF NOT EXISTS episodes (
        id         TEXT PRIMARY KEY,
        ts         REAL    NOT NULL,
        session_id TEXT    DEFAULT '',
        text       TEXT    NOT NULL,
        meta_json  TEXT    DEFAULT '{}',
        prism_type TEXT    DEFAULT 'episode' -- episode | spectrum_report
    );
    CREATE INDEX IF NOT EXISTS idx_episodes_ts ON episodes(ts DESC);
"""


class EpisodicStore:
    """
    SQLite WAL-backed episodic memory store.
    Drop-in replacement for the episodic.jsonl append pattern in Memory.
    Thread-safe via WAL mode — multiple readers, one writer at a time.
    """

    def __init__(self, workspace: Path) -> None:
        self._db_path = workspace / "memory" / "episodic.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        # v27: Set up FTS5 virtual table for full-text search
        _episodic_add_fts(self)
        # Migrate existing episodic.jsonl if present
        self._migrate_jsonl(workspace / "memory" / "episodic.jsonl")

    def _conn(self) -> Any:
        import sqlite3 as _sq3
        conn = _sq3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_EPISODIC_DDL)

    def _migrate_jsonl(self, jsonl_path: Path) -> None:
        """One-shot migration: import episodes from legacy episodic.jsonl."""
        if not jsonl_path.exists():
            return
        migrated = 0
        try:
            import sqlite3 as _sq3
            with self._conn() as conn:
                existing = {r[0] for r in
                            conn.execute("SELECT id FROM episodes").fetchall()}
                with open(jsonl_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ep = _fast_loads(line)
                            eid = ep.get("id") or hashlib.sha256(
                                line.encode()).hexdigest()[:16]
                            if eid in existing:
                                continue
                            conn.execute(
                                "INSERT OR IGNORE INTO episodes "
                                "(id, ts, session_id, text, meta_json) VALUES (?,?,?,?,?)",
                                (eid,
                                 float(ep.get("ts", 0)),
                                 str(ep.get("session_id", "")),
                                 str(ep.get("text", "")),
                                 _fast_dumps(ep.get("meta", {}))))
                            migrated += 1
                        except Exception:
                            pass
            if migrated > 0:
                log.info("episodic_jsonl_migrated",
                         extra={"count": migrated,
                                "source": str(jsonl_path)})
        except Exception as _e:
            log.debug("episodic_migration_error",
                      extra={"error": str(_e)[:80]})

    def record(self, text: str, session_id: str = "",
                metadata: dict | None = None, prism_type: str = "episode") -> str:
        """Append an episode or Analytics Engine report. Returns the episode ID."""
        eid  = secrets.token_hex(8)
        now  = time.time()
        meta = _fast_dumps(metadata or {})
        import sqlite3 as _sq3
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO episodes (id, ts, session_id, text, meta_json, prism_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (eid, now, session_id, text[:8000], meta, prism_type))
        return eid

    def recent(self, n: int = 20, session_id: str = "") -> list[dict]:
        """Return the N most recent episodes, optionally filtered by session."""
        import sqlite3 as _sq3
        with self._conn() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT id, ts, session_id, text, meta_json "
                    "FROM episodes WHERE session_id=? "
                    "ORDER BY ts DESC LIMIT ?",
                    (session_id, n)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, ts, session_id, text, meta_json "
                    "FROM episodes ORDER BY ts DESC LIMIT ?",
                    (n,)).fetchall()
        return [{"id": r[0], "ts": r[1], "session_id": r[2],
                 "text": r[3], "meta": _fast_loads(r[4])}
                for r in rows]

    def count(self) -> int:
        """Total episode count."""
        import sqlite3 as _sq3
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

    def search(self, query: str, n: int = 10) -> list[dict]:
        """Full-text search using FTS5. Delegates to episodic_search()."""
        return episodic_search(self, query, n)

    def prune_before(self, cutoff_ts: float) -> int:
        """Delete episodes older than cutoff_ts. Returns rows deleted."""
        import sqlite3 as _sq3
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM episodes WHERE ts < ?", (cutoff_ts,))
            return cursor.rowcount


# ══════════════════════════════════════════════════════════════════════════════
