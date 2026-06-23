
"""Capsule Store migrations 0050–0053."""
from __future__ import annotations
import sqlite3
from pathlib import Path


_MIGRATIONS: list[tuple[str, str]] = [
    ("0050", """
        CREATE TABLE IF NOT EXISTS apde_capsules (
            id TEXT PRIMARY KEY, raw_prompt TEXT NOT NULL,
            goal TEXT NOT NULL, success_signals TEXT NOT NULL,
            artifacts TEXT NOT NULL, budget TEXT NOT NULL,
            constraints TEXT NOT NULL, out_of_scope TEXT NOT NULL,
            apde_role TEXT NOT NULL DEFAULT 'intent',
            lifecycle_state TEXT NOT NULL DEFAULT 'draft',
            runtime_manifest_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )
    """),
    ("0051", """
        CREATE TABLE IF NOT EXISTS apde_plans (
            id TEXT PRIMARY KEY, capsule_id TEXT NOT NULL,
            tasks_json TEXT NOT NULL, plan_hash TEXT NOT NULL,
            plan_status TEXT NOT NULL DEFAULT 'DRAFT',
            runtime_manifest_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )
    """),
    ("0052", """
        CREATE TABLE IF NOT EXISTS apde_plan_deltas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT NOT NULL, seq INTEGER NOT NULL,
            delta_type TEXT NOT NULL, payload TEXT NOT NULL,
            ts REAL NOT NULL
        )
    """),
    ("0053", """
        CREATE TABLE IF NOT EXISTS sqe_stratum_state (
            runtime_id TEXT NOT NULL, stratum_key TEXT NOT NULL,
            mean REAL NOT NULL DEFAULT 0.0,
            variance REAL NOT NULL DEFAULT 0.0,
            n INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (runtime_id, stratum_key)
        )
    """),
]


def apply_migrations(db_path: Path) -> list[str]:
    """Apply migrations 0050-0053 idempotently. Returns list of applied ids."""
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS apde_schema_migrations (
            id TEXT PRIMARY KEY, applied_at REAL NOT NULL
        )
    """)
    conn.commit()
    applied: list[str] = []
    import time
    for mid, sql in _MIGRATIONS:
        exists = conn.execute(
            "SELECT id FROM apde_schema_migrations WHERE id=?", (mid,)
        ).fetchone()
        if exists:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO apde_schema_migrations VALUES (?, ?)",
            (mid, time.time())
        )
        conn.commit()
        applied.append(mid)
    conn.close()
    return applied
