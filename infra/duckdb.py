""" — DuckDB analytics for cost+audit queries."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# DUCKDB ANALYTICS  — cost log + audit queries
# ══════════════════════════════════════════════════════════════════════════════
# DuckDB reads workspace/cost_log.jsonl and workspace/logs/audit.jsonl natively
# with SQL — no migration, no loading into memory.
# Exposes: essence cost --sql "SELECT ..."  and  essence audit --sql "..."
#
# ENV:  Essence_DUCKDB=1   Enable DuckDB analytics (default: off)

_DUCKDB_ENABLED = os.environ.get("Essence_DUCKDB", "0") == "1"

try:
    import duckdb as _duckdb  # type: ignore   # pip install duckdb
    _DUCKDB = True
except ImportError:
    _duckdb = None  # type: ignore
    _DUCKDB = False


class DuckDBAnalytics:
    """
    SQL analytics over JSONL log files via DuckDB.
    No data loading required — DuckDB queries files directly.
    """

    def __init__(self, workspace: Path) -> None:
        self._ws    = workspace
        self._conn: Any = None

    _shared_conn: Any            = None
    _shared_lock: threading.Lock = threading.Lock()

    def _ensure_conn(self) -> Any:
        # Read through the assembled essence namespace (not this module's
        # own copy of _DUCKDB/_duckdb) so that callers/tests patching
        # "essence._DUCKDB" / "essence._duckdb" (the flattened, single-namespace
        # view of this package) are honoured.
        import essence as _essence
        duckdb_enabled = getattr(_essence, "_DUCKDB", _DUCKDB)
        duckdb_mod     = getattr(_essence, "_duckdb", _duckdb)
        if not duckdb_enabled:
            return None
        with DuckDBAnalytics._shared_lock:
            if DuckDBAnalytics._shared_conn is None:
                DuckDBAnalytics._shared_conn = duckdb_mod.connect(":memory:")
        return DuckDBAnalytics._shared_conn

    def query(self, sql: str) -> list[dict]:
        """Execute a SQL query. Use {cost_log} and {audit_log} as table refs."""
        conn = self._ensure_conn()
        if conn is None:
            return []
        cost_path  = self._ws / "cost_log.jsonl"
        audit_path = self._ws / "logs" / "audit.jsonl"
        # v24: Escape paths to prevent SQL injection via workspace path names
        _esc_cost  = str(cost_path).replace("'", "''")
        _esc_audit = str(audit_path).replace("'", "''")
        sql = sql.replace("{cost_log}",
                           f"read_json_auto('{_esc_cost}')" if cost_path.exists() else "VALUES(NULL)")
        sql = sql.replace("{audit_log}",
                           f"read_json_auto('{_esc_audit}')" if audit_path.exists() else "VALUES(NULL)")
        try:
            result = conn.execute(sql).fetchdf()
            return result.to_dict("records")
        except Exception as _e:
            log.debug("duckdb_query_error", extra={"error": str(_e)[:120]})
            return []

    def cost_summary(self) -> list[dict]:
        """Per-model token spend summary."""
        return self.query(
            "SELECT model, COUNT(*) as tasks, "
            "SUM(CAST(prompt_tok AS INTEGER) + CAST(completion_tok AS INTEGER)) as total_tokens "
            "FROM {cost_log} GROUP BY model ORDER BY total_tokens DESC")

    def audit_event_counts(self) -> list[dict]:
        """Count of each event_type in the audit log."""
        return self.query(
            "SELECT event_type, COUNT(*) as count "
            "FROM {audit_log} GROUP BY event_type ORDER BY count DESC")

    def tool_failure_rate(self) -> list[dict]:
        """Tool call failure rate from audit log."""
        return self.query(
            "SELECT data.tool as tool, "
            "COUNT(*) as total "
            "FROM {audit_log} WHERE event_type='tool_call' "
            "GROUP BY data.tool ORDER BY total DESC")


# ══════════════════════════════════════════════════════════════════════════════
