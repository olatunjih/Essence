
"""SQE stratum store: per-stratum running mean and variance."""
from __future__ import annotations
import math, sqlite3, threading, time
from pathlib import Path


class StratumStore:
    """
    Persistent per-stratum running mean and variance (Welford's algorithm).
    Scoped to a runtime_id so manifests don't bleed across boot epochs.
    """

    def __init__(self, db_path: Path, runtime_id: str) -> None:
        self._db         = str(db_path)
        self._runtime_id = runtime_id
        self._lock       = threading.Lock()
        self._ensure_table()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS sqe_stratum_state (
                    runtime_id  TEXT NOT NULL,
                    stratum_key TEXT NOT NULL,
                    mean        REAL NOT NULL DEFAULT 0.0,
                    variance    REAL NOT NULL DEFAULT 0.0,
                    n           INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (runtime_id, stratum_key)
                )
            """)

    def _load(self, c: sqlite3.Connection, key: str) -> tuple[float, float, int]:
        row = c.execute(
            "SELECT mean, variance, n FROM sqe_stratum_state "
            "WHERE runtime_id=? AND stratum_key=?",
            (self._runtime_id, key)
        ).fetchone()
        if row:
            return float(row["mean"]), float(row["variance"]), int(row["n"])
        return 0.0, 0.0, 0

    def update(self, stratum_key: str, score: float) -> None:
        """Update running mean and variance for stratum_key with new score."""
        with self._lock, self._conn() as c:
            mean, var, n = self._load(c, stratum_key)
            n_new  = n + 1
            delta  = score - mean
            mean_new = mean + delta / n_new
            delta2   = score - mean_new
            var_new  = var + delta * delta2
            c.execute("""
                INSERT OR REPLACE INTO sqe_stratum_state
                (runtime_id, stratum_key, mean, variance, n)
                VALUES (?,?,?,?,?)
            """, (self._runtime_id, stratum_key, mean_new, var_new, n_new))

    def get(self, stratum_key: str) -> dict:
        """Return {mean, std, n} for stratum_key."""
        with self._conn() as c:
            mean, var, n = self._load(c, stratum_key)
        std = math.sqrt(var / n) if n > 1 else 0.0
        return {"mean": mean, "std": std, "n": n, "stratum_key": stratum_key}

    def all_strata(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT stratum_key, mean, variance, n FROM sqe_stratum_state "
                "WHERE runtime_id=?", (self._runtime_id,)
            ).fetchall()
        result = []
        for row in rows:
            n    = int(row["n"])
            var  = float(row["variance"])
            std  = math.sqrt(var / n) if n > 1 else 0.0
            result.append({
                "stratum_key": row["stratum_key"],
                "mean": float(row["mean"]),
                "std":  std,
                "n":    n,
            })
        return result
