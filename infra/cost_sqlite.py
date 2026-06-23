""" — WAL-safe cost log."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.orjson_shim import _fast_loads  # noqa: F401  [real source bug]

# COST LOG SQLITE WAL  — concurrent-write safe cost tracking
# ══════════════════════════════════════════════════════════════════════════════
# Replaces cost_log.jsonl with a SQLite WAL table.
# Same concurrent-write problem as episodic.jsonl — consolidation job reads
# while active session writes. WAL mode eliminates the race.
# DuckDB reads both SQLite and JSONL natively — no analytics changes needed.
# Schema: (id, ts, task_id, model, prompt_tok, completion_tok, cost_usd, session_id)

_COST_SQLITE_DDL = """
    CREATE TABLE IF NOT EXISTS cost_log (
        id             TEXT PRIMARY KEY,
        ts             REAL    NOT NULL,
        task_id        TEXT    DEFAULT '',
        model          TEXT    DEFAULT '',
        prompt_tok     INTEGER DEFAULT 0,
        completion_tok INTEGER DEFAULT 0,
        cost_usd       REAL    DEFAULT 0.0,
        session_id     TEXT    DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_cost_ts    ON cost_log(ts DESC);
    CREATE INDEX IF NOT EXISTS idx_cost_model ON cost_log(model);
"""


class CostSQLite:
    """
    SQLite WAL-backed cost log store.
    Thread-safe; one writer at a time via WAL mode.
    Backward-compatible: existing cost_log.jsonl is migrated on first open.
    """

    def __init__(self, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        self._db  = workspace / "cost.db"
        self._legacy = workspace / "cost_log.jsonl"
        self._init_db()
        self._migrate_jsonl()

    def _conn(self) -> Any:
        import sqlite3 as _sq3
        conn = _sq3.connect(str(self._db), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_COST_SQLITE_DDL)

    def _migrate_jsonl(self) -> None:
        if not self._legacy.exists():
            return
        migrated = 0
        try:
            import sqlite3 as _sq3
            with self._conn() as conn:
                existing = {r[0] for r in
                            conn.execute("SELECT id FROM cost_log").fetchall()}
                with open(self._legacy, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row  = _fast_loads(line)
                            rid  = row.get("task_id") or hashlib.sha256(
                                line.encode()).hexdigest()[:16]
                            if rid in existing:
                                continue
                            conn.execute(
                                "INSERT OR IGNORE INTO cost_log "
                                "(id,ts,task_id,model,prompt_tok,completion_tok,cost_usd) "
                                "VALUES(?,?,?,?,?,?,?)",
                                (rid,
                                 float(row.get("ts", 0)),
                                 str(row.get("task_id", "")),
                                 str(row.get("model", "")),
                                 int(row.get("prompt_tok", 0)),
                                 int(row.get("completion_tok", 0)),
                                 float(row.get("cost_usd", 0.0))))
                            migrated += 1
                        except Exception:
                            pass
            if migrated:
                log.info("cost_jsonl_migrated", extra={"count": migrated})
        except Exception as _e:
            log.debug("cost_migration_error", extra={"error": str(_e)[:80]})

    def record(self, task_id: str, model: str, prompt_tok: int,
                completion_tok: int, cost_usd: float,
                session_id: str = "") -> None:
        rid = secrets.token_hex(8)
        import sqlite3 as _sq3
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cost_log "
                "(id,ts,task_id,model,prompt_tok,completion_tok,cost_usd,session_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (rid, time.time(), task_id, model,
                 prompt_tok, completion_tok, cost_usd, session_id))

    def summary(self) -> list[dict]:
        """Per-model totals."""
        import sqlite3 as _sq3
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT model, COUNT(*) tasks, "
                "SUM(prompt_tok+completion_tok) total_tokens, "
                "SUM(cost_usd) total_cost "
                "FROM cost_log GROUP BY model "
                "ORDER BY total_tokens DESC").fetchall()
        return [{"model": r[0], "tasks": r[1],
                 "total_tokens": r[2], "total_cost": round(r[3], 6)}
                for r in rows]

    def total_tokens(self) -> int:
        """Return total token count across all recorded tasks."""
        import sqlite3 as _sq3
        with self._conn() as conn:
            r = conn.execute(
                "SELECT COALESCE(SUM(prompt_tok+completion_tok),0) "
                "FROM cost_log").fetchone()
        return int(r[0])

    # ── Fix 9: Per-user quota persistence ─────────────────────────────────────

    def _ensure_quota_table(self) -> None:
        """Create the per-user quota table if it does not yet exist."""
        import sqlite3 as _sq3
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS quota_usage (
                    user_id    TEXT PRIMARY KEY,
                    consumed   INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL    NOT NULL DEFAULT 0
                )
            """)

    def get_quota(self, user_id: str) -> int:
        """
        Return the current quota consumption for user_id.

        Fix 9: GuardrailLayer calls this instead of its in-memory dict so
        consumption survives process restarts.

        Returns:
            Integer consumption count; 0 if user has no recorded usage.
        """
        import sqlite3 as _sq3
        self._ensure_quota_table()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT consumed FROM quota_usage WHERE user_id=?",
                (user_id,)
            ).fetchone()
        return int(row[0]) if row else 0

    def increment_quota(self, user_id: str, amount: int = 1) -> None:
        """
        Increment the recorded quota consumption for user_id.

        Fix 9: GuardrailLayer calls this on each successful g2_quota check so
        the consumed amount is durably persisted.

        Args:
            user_id: The user whose quota to increment.
            amount:  Number of units to add (default 1 per call).
        """
        import sqlite3 as _sq3
        self._ensure_quota_table()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO quota_usage (user_id, consumed, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    consumed   = consumed + excluded.consumed,
                    updated_at = excluded.updated_at
            """, (user_id, amount, time.time()))


# ══════════════════════════════════════════════════════════════════════════════
