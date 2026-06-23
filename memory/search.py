""" — FTS5 full-text search over episodic memory."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.orjson_shim import _fast_loads  # noqa: F401  [real source bug]

# FULL-TEXT SEARCH OVER EPISODIC MEMORY  — SQLite FTS5
# ══════════════════════════════════════════════════════════════════════════════
# Adds FTS5 virtual table and trigger to EpisodicStore.
# EpisodicStore.search(query, n) returns ranked matches in sub-millisecond.
# Replaces the O(n) keyword scan in Memory.recall().

_EPISODIC_FTS_DDL = """
    CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts
    USING fts5(text, content=episodes, content_rowid=rowid);

    CREATE TRIGGER IF NOT EXISTS episodes_ai
    AFTER INSERT ON episodes BEGIN
        INSERT INTO episodes_fts(rowid, text) VALUES (new.rowid, new.text);
    END;

    CREATE TRIGGER IF NOT EXISTS episodes_ad
    AFTER DELETE ON episodes BEGIN
        INSERT INTO episodes_fts(episodes_fts, rowid, text)
        VALUES ('delete', old.rowid, old.text);
    END;

    CREATE TRIGGER IF NOT EXISTS episodes_au
    AFTER UPDATE ON episodes BEGIN
        INSERT INTO episodes_fts(episodes_fts, rowid, text)
        VALUES ('delete', old.rowid, old.text);
        INSERT INTO episodes_fts(rowid, text) VALUES (new.rowid, new.text);
    END;
"""


def _episodic_add_fts(store: "EpisodicStore") -> None:
    """
    Add FTS5 virtual table and sync triggers to an existing EpisodicStore.
    Idempotent — safe to call on every startup.
    Backfills the FTS index from existing episodes if the table was just created.
    """
    try:
        with store._conn() as conn:
            conn.executescript(_EPISODIC_FTS_DDL)
            # Backfill: insert any existing rows not yet in FTS
            conn.execute(
                "INSERT INTO episodes_fts(rowid, text) "
                "SELECT rowid, text FROM episodes "
                "WHERE rowid NOT IN (SELECT rowid FROM episodes_fts)")
    except Exception as _e:
        log.debug("episodic_fts_setup_error", extra={"error": str(_e)[:80]})


def episodic_search(store: "EpisodicStore",
                     query: str, n: int = 10) -> list[dict]:
    """
    Full-text search over episodic memory using SQLite FTS5.
    Returns ranked matches (highest BM25 relevance first).
    Falls back to EpisodicStore.recent(n) when FTS is unavailable.
    """
    try:
        import sqlite3 as _sq3
        with store._conn() as conn:
            # Check FTS table exists
            tables = {r[0] for r in
                      conn.execute("SELECT name FROM sqlite_master "
                                   "WHERE type='table'").fetchall()}
            if "episodes_fts" not in tables:
                _episodic_add_fts(store)
            # FTS5 MATCH query with BM25 ranking
            rows = conn.execute(
                "SELECT e.id, e.ts, e.session_id, e.text, e.meta_json, "
                "       bm25(episodes_fts) rank "
                "FROM episodes e "
                "JOIN episodes_fts f ON e.rowid = f.rowid "
                "WHERE episodes_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (query, n)).fetchall()
        return [{"id": r[0], "ts": r[1], "session_id": r[2],
                 "text": r[3], "meta": _fast_loads(r[4]),
                 "rank": r[5]}
                for r in rows]
    except Exception as _e:
        log.debug("episodic_fts_search_error", extra={"error": str(_e)[:80]})
        return store.recent(n)


# ══════════════════════════════════════════════════════════════════════════════
