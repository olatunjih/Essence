""" — persistent A2A delivery retry."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.orjson_shim import _fast_dumps, _fast_loads  # noqa: F401  [real source bug]

# PERSISTENT RETRY QUEUE  — durable A2A delivery
# ══════════════════════════════════════════════════════════════════════════════
# Stores failed A2A task deliveries in SQLite with exponential backoff.
# HeartbeatScheduler drives retry via registered job "retry_queue_flush".
# No new dependencies — SQLite is stdlib.
#
# ENV:
#   Essence_RETRY_MAX_ATTEMPTS=5    Max delivery attempts before permanent failure
#   Essence_RETRY_BASE_DELAY=30     Base backoff delay in seconds

_RETRY_MAX_ATTEMPTS = int(os.environ.get("Essence_RETRY_MAX_ATTEMPTS", "5"))
_RETRY_BASE_DELAY   = int(os.environ.get("Essence_RETRY_BASE_DELAY",   "30"))


class RetryQueue:
    """
    Persistent retry queue backed by SQLite.
    Items have: id, payload (JSON), attempts, next_attempt_ts, created_ts.
    Exponential backoff: delay = base_delay * 2^attempts (capped at 1 hour).
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS retry_queue (
            id            TEXT PRIMARY KEY,
            queue_name    TEXT NOT NULL,
            payload       TEXT NOT NULL,
            attempts      INTEGER DEFAULT 0,
            next_attempt  REAL NOT NULL,
            created_at    REAL NOT NULL,
            last_error    TEXT DEFAULT ''
        )
    """

    def __init__(self, workspace: Path, queue_name: str = "a2a") -> None:
        self._path  = workspace / "logs" / "retry_queue.db"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._name  = queue_name
        self._lock  = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        import sqlite3 as _sq3
        with _sq3.connect(str(self._path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(self._DDL)
            conn.commit()

    def enqueue(self, item_id: str, payload: dict) -> None:
        """Add item to retry queue for immediate first attempt."""
        import sqlite3 as _sq3
        with self._lock:
            with _sq3.connect(str(self._path)) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO retry_queue "
                    "(id, queue_name, payload, attempts, next_attempt, created_at) "
                    "VALUES (?, ?, ?, 0, ?, ?)",
                    (item_id, self._name, _fast_dumps(payload, default=str),
                     time.time(), time.time()))
                conn.commit()

    def due(self) -> list[dict]:
        """Return items due for retry (next_attempt <= now)."""
        import sqlite3 as _sq3
        now = time.time()
        with self._lock:
            with _sq3.connect(str(self._path)) as conn:
                rows = conn.execute(
                    "SELECT id, payload, attempts FROM retry_queue "
                    "WHERE queue_name=? AND next_attempt<=? "
                    "ORDER BY next_attempt LIMIT 50",
                    (self._name, now)).fetchall()
        return [{"id": r[0], "payload": _fast_loads(r[1]), "attempts": r[2]}
                for r in rows]

    def mark_success(self, item_id: str) -> None:
        import sqlite3 as _sq3
        with self._lock:
            with _sq3.connect(str(self._path)) as conn:
                conn.execute("DELETE FROM retry_queue WHERE id=?", (item_id,))
                conn.commit()

    def mark_failure(self, item_id: str, error: str = "") -> None:
        """Reschedule with exponential backoff; delete after max attempts."""
        import sqlite3 as _sq3
        with self._lock:
            with _sq3.connect(str(self._path)) as conn:
                row = conn.execute(
                    "SELECT attempts FROM retry_queue WHERE id=?",
                    (item_id,)).fetchone()
                if row is None:
                    return
                attempts = row[0] + 1
                if attempts >= _RETRY_MAX_ATTEMPTS:
                    conn.execute("DELETE FROM retry_queue WHERE id=?", (item_id,))
                    log.warning("retry_queue_exhausted",
                                extra={"id": item_id, "attempts": attempts})
                else:
                    delay     = min(_RETRY_BASE_DELAY * (2 ** attempts), 3600)
                    next_time = time.time() + delay
                    conn.execute(
                        "UPDATE retry_queue SET attempts=?, next_attempt=?, last_error=? "
                        "WHERE id=?",
                        (attempts, next_time, error[:200], item_id))
                conn.commit()

    def size(self) -> int:
        import sqlite3 as _sq3
        with self._lock:
            with _sq3.connect(str(self._path)) as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM retry_queue WHERE queue_name=?",
                    (self._name,)).fetchone()[0]


_retry_queue: "RetryQueue | None" = None


def get_retry_queue(workspace: Path | None = None) -> "RetryQueue | None":
    global _retry_queue
    if _retry_queue is None and workspace:
        _retry_queue = RetryQueue(workspace)
    return _retry_queue


# ══════════════════════════════════════════════════════════════════════════════
