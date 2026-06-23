
"""
Capsule Store repositories: CapsuleRepository, PlanRepository, DeltaLedger.
Implements NN-2 (state-change gating) and Axiom A2 (frozen plan + delta ledger).

Fix 4: DeltaLedger gains get_artifact() and record_artifact() so execution
       results can be persisted and retrieved by task context population.
Fix 6: PlanRepository gains get_active_plan(capsule_id) so Kernel.tick() and
       user_input() look up plans by capsule_id, not by a derived plan ID.
"""
from __future__ import annotations
import dataclasses as _dc, hashlib, json, sqlite3, threading, time, uuid
from pathlib import Path
from typing import Any
from essence.apde_types import (
    IntentCapsule, Task, PlanDAG, PlanDeltaRow,
    TaskState, PlanStatus, AxiomViolation,
)
from essence.infra.capsule_store.canonicalization import hash_capsule


# ── Database path ─────────────────────────────────────────────────────────────

def _db_path(workspace: Path) -> Path:
    """Return the canonical capsule store path inside a workspace directory."""
    p = workspace / "capsule_store.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── CapsuleRepository ─────────────────────────────────────────────────────────

class CapsuleRepository:
    """Persistent store for IntentCapsule objects.

    All writes are protected by a threading.Lock so the repository is safe
    for concurrent access within a single process.  Cross-process access
    relies on SQLite's built-in WAL mode (enabled by migrations).
    """

    def __init__(self, db_path: Path) -> None:
        self._db = str(db_path)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Create all capsule-store tables if they do not yet exist."""
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS apde_capsules (
                    id                   TEXT PRIMARY KEY,
                    raw_prompt           TEXT NOT NULL,
                    goal                 TEXT NOT NULL,
                    success_signals      TEXT NOT NULL,
                    artifacts            TEXT NOT NULL,
                    budget               TEXT NOT NULL,
                    constraints          TEXT NOT NULL,
                    out_of_scope         TEXT NOT NULL,
                    apde_role            TEXT NOT NULL DEFAULT 'intent',
                    lifecycle_state      TEXT NOT NULL DEFAULT 'draft',
                    runtime_manifest_id  TEXT NOT NULL DEFAULT '',
                    created_at           REAL NOT NULL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS apde_plans (
                    id                   TEXT PRIMARY KEY,
                    capsule_id           TEXT NOT NULL,
                    tasks_json           TEXT NOT NULL,
                    plan_hash            TEXT NOT NULL,
                    plan_status          TEXT NOT NULL DEFAULT 'DRAFT',
                    runtime_manifest_id  TEXT NOT NULL DEFAULT '',
                    created_at           REAL NOT NULL
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_apde_plans_capsule_id
                ON apde_plans (capsule_id)
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS apde_plan_deltas (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id     TEXT NOT NULL,
                    seq         INTEGER NOT NULL,
                    delta_type  TEXT NOT NULL,
                    payload     TEXT NOT NULL,
                    ts          REAL NOT NULL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS sqe_stratum_state (
                    runtime_id   TEXT NOT NULL,
                    stratum_key  TEXT NOT NULL,
                    mean         REAL NOT NULL DEFAULT 0.0,
                    variance     REAL NOT NULL DEFAULT 0.0,
                    n            INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (runtime_id, stratum_key)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS apde_artifacts (
                    capsule_id  TEXT NOT NULL,
                    key         TEXT NOT NULL,
                    value_json  TEXT NOT NULL,
                    ts          REAL NOT NULL,
                    PRIMARY KEY (capsule_id, key)
                )
            """)

    def save(self, capsule: IntentCapsule) -> None:
        """Persist or replace an IntentCapsule by its id."""
        with self._lock, self._conn() as c:
            c.execute("""
                INSERT OR REPLACE INTO apde_capsules VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                capsule.id,
                capsule.raw_prompt,
                capsule.goal,
                json.dumps(capsule.success_signals),
                json.dumps(capsule.artifacts),
                json.dumps(capsule.budget),
                json.dumps(capsule.constraints),
                json.dumps(capsule.out_of_scope),
                capsule.apde_role,
                capsule.lifecycle_state,
                capsule.runtime_manifest_id,
                capsule.created_at,
            ))

    def get(self, capsule_id: str) -> IntentCapsule | None:
        """Retrieve an IntentCapsule by id.  Returns None if not found."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM apde_capsules WHERE id=?", (capsule_id,)
            ).fetchone()
        if row is None:
            return None
        return IntentCapsule(
            id=row["id"],
            raw_prompt=row["raw_prompt"],
            goal=row["goal"],
            success_signals=json.loads(row["success_signals"]),
            artifacts=json.loads(row["artifacts"]),
            budget=json.loads(row["budget"]),
            constraints=json.loads(row["constraints"]),
            out_of_scope=json.loads(row["out_of_scope"]),
            apde_role=row["apde_role"],
            lifecycle_state=row["lifecycle_state"],
            runtime_manifest_id=row["runtime_manifest_id"],
            created_at=row["created_at"],
        )

    def assert_columns_exist(self) -> None:
        """NN-2: assert required columns exist; raises RuntimeError if missing."""
        required = {
            "apde_capsules": {"id", "raw_prompt", "goal", "success_signals",
                               "artifacts", "budget", "constraints", "out_of_scope",
                               "apde_role", "lifecycle_state", "runtime_manifest_id",
                               "created_at"},
            "apde_plans": {"id", "capsule_id", "tasks_json", "plan_hash",
                           "plan_status", "runtime_manifest_id", "created_at"},
            "apde_plan_deltas": {"id", "plan_id", "seq", "delta_type", "payload", "ts"},
        }
        with self._conn() as c:
            for table, cols in required.items():
                rows = c.execute(f"PRAGMA table_info({table})").fetchall()
                existing = {r["name"] for r in rows}
                missing = cols - existing
                if missing:
                    raise RuntimeError(
                        f"Capsule Store missing columns in {table}: {missing}")


# ── PlanRepository ────────────────────────────────────────────────────────────

class PlanRepository:
    """
    Repository for PlanDAG objects.

    Enforces Axiom A2: plan_hash is frozen on first save; subsequent saves
    only allowed if plan_status is transitioning through the state machine.

    Fix 6: get_active_plan(capsule_id) looks up plans by capsule_id with
    ACTIVE status so Kernel.tick() and user_input() do not need to reconstruct
    a plan_id from the capsule_id (which would break when plan IDs are uuid4).

    Note: PlanRepository requires a database initialised by CapsuleRepository
    first (which owns the shared schema creation).  Both repositories must be
    constructed against the same db_path; boot_kernel() guarantees this
    ordering.  If you need PlanRepository independently, construct a
    CapsuleRepository against the same path first.
    """

    def __init__(self, db_path: Path) -> None:
        self._db   = str(db_path)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create plan-related tables if they do not yet exist.

        CapsuleRepository._ensure_schema() also creates these tables;
        this method is idempotent (CREATE TABLE IF NOT EXISTS) so calling
        both is safe and makes PlanRepository independently usable.
        """
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS apde_plans (
                    id                   TEXT PRIMARY KEY,
                    capsule_id           TEXT NOT NULL,
                    tasks_json           TEXT NOT NULL,
                    plan_hash            TEXT NOT NULL,
                    plan_status          TEXT NOT NULL DEFAULT 'DRAFT',
                    runtime_manifest_id  TEXT NOT NULL DEFAULT '',
                    created_at           REAL NOT NULL
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_apde_plans_capsule_id
                ON apde_plans (capsule_id)
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS apde_plan_deltas (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id     TEXT NOT NULL,
                    seq         INTEGER NOT NULL,
                    delta_type  TEXT NOT NULL,
                    payload     TEXT NOT NULL,
                    ts          REAL NOT NULL
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _tasks_to_json(self, tasks: list[Task]) -> str:
        """Serialize a list of Task objects to JSON for storage."""
        def _t(t: Task) -> dict:
            return {
                "id": t.id, "capsule_id": t.capsule_id, "goal": t.goal,
                "reads": t.reads, "writes": t.writes, "tools": t.tools,
                "state": t.state.value, "risk": t.risk.value,
                "parent_id": t.parent_id, "subtask_ids": t.subtask_ids,
                "done_when": t.done_when, "result": t.result,
                "token_usage": t.token_usage,
            }
        return json.dumps([_t(t) for t in tasks])

    def _tasks_from_json(self, raw: str, capsule_id: str) -> list[Task]:
        """Deserialize a JSON string into a list of Task objects."""
        from essence.apde_types import RiskLevel
        tasks = []
        for d in json.loads(raw):
            t = Task(
                id=d["id"], capsule_id=capsule_id, goal=d["goal"],
                reads=d.get("reads", []), writes=d.get("writes", []),
                tools=d.get("tools", []),
                state=TaskState(d.get("state", "READY")),
                risk=RiskLevel(d.get("risk", "LOW")),
                parent_id=d.get("parent_id", ""),
                subtask_ids=d.get("subtask_ids", []),
                done_when=d.get("done_when", ""),
                result=d.get("result", ""),
                token_usage=d.get("token_usage", 0),
            )
            tasks.append(t)
        return tasks

    def save(self, plan: PlanDAG) -> None:
        """Persist or update a PlanDAG.

        If the plan already exists, validates that plan_hash has not changed (A2).
        Only plan_status and tasks_json are mutable after initial save.
        """
        with self._lock, self._conn() as c:
            existing = c.execute(
                "SELECT plan_hash FROM apde_plans WHERE id=?", (plan.id,)
            ).fetchone()
            if existing is not None:
                if existing["plan_hash"] != plan.plan_hash:
                    raise AxiomViolation(
                        f"Plan {plan.id}: plan_hash may not change after freezing (A2). "
                        f"Use DeltaLedger for plan modifications.")
            c.execute("""
                INSERT OR REPLACE INTO apde_plans VALUES (?,?,?,?,?,?,?)
            """, (
                plan.id,
                plan.capsule_id,
                self._tasks_to_json(plan.tasks),
                plan.plan_hash,
                plan.plan_status.value,
                plan.runtime_manifest_id,
                time.time(),
            ))

    def get(self, plan_id: str) -> PlanDAG | None:
        """Retrieve a PlanDAG by its plan_id.  Returns None if not found."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM apde_plans WHERE id=?", (plan_id,)
            ).fetchone()
        if row is None:
            return None
        plan = PlanDAG(
            id=row["id"],
            capsule_id=row["capsule_id"],
            plan_hash=row["plan_hash"],
            plan_status=PlanStatus(row["plan_status"]),
            runtime_manifest_id=row["runtime_manifest_id"],
        )
        plan.tasks = self._tasks_from_json(row["tasks_json"], plan.capsule_id)
        return plan

    def get_active_plan(self, capsule_id: str) -> PlanDAG | None:
        """
        Return the ACTIVE plan for a capsule, or None if none exists.

        Fix 6: Kernel.tick() and user_input() must not reconstruct plan IDs
        from capsule IDs (a uuid5 derivation that breaks when plan IDs are
        uuid4-generated by Decomposer).  Instead they call this method to
        look up the plan by capsule_id and ACTIVE status.

        If multiple ACTIVE plans exist for the same capsule (abnormal),
        the most recently created one is returned.
        """
        with self._conn() as c:
            row = c.execute(
                """SELECT * FROM apde_plans
                   WHERE capsule_id=? AND plan_status='ACTIVE'
                   ORDER BY created_at DESC LIMIT 1""",
                (capsule_id,)
            ).fetchone()
        if row is None:
            return None
        plan = PlanDAG(
            id=row["id"],
            capsule_id=row["capsule_id"],
            plan_hash=row["plan_hash"],
            plan_status=PlanStatus(row["plan_status"]),
            runtime_manifest_id=row["runtime_manifest_id"],
        )
        plan.tasks = self._tasks_from_json(row["tasks_json"], plan.capsule_id)
        return plan

    def list_active(self) -> list[PlanDAG]:
        """Return all plans that are not ABORTED, ordered by created_at desc."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM apde_plans
                   WHERE plan_status != 'ABORTED'
                   ORDER BY created_at DESC"""
            ).fetchall()
        result = []
        for row in rows:
            plan = PlanDAG(
                id=row["id"],
                capsule_id=row["capsule_id"],
                plan_hash=row["plan_hash"],
                plan_status=PlanStatus(row["plan_status"]),
                runtime_manifest_id=row["runtime_manifest_id"],
            )
            plan.tasks = self._tasks_from_json(row["tasks_json"], plan.capsule_id)
            result.append(plan)
        return result

    def update_task_state(self, plan_id: str, task_id: str,
                          new_state: TaskState) -> None:
        """Update a single task's state within a plan.

        Deserializes tasks, applies the state transition (which validates it
        via TaskState.can_transition_to), then re-serializes.
        """
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT tasks_json, capsule_id FROM apde_plans WHERE id=?",
                (plan_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Plan {plan_id} not found")
            tasks = self._tasks_from_json(row["tasks_json"], row["capsule_id"])
            updated = False
            for t in tasks:
                if t.id == task_id:
                    t.transition(new_state)
                    updated = True
                    break
            if not updated:
                raise KeyError(f"Task {task_id} not in plan {plan_id}")
            c.execute(
                "UPDATE apde_plans SET tasks_json=? WHERE id=?",
                (self._tasks_to_json(tasks), plan_id)
            )

    def update_status(self, plan_id: str, new_status: PlanStatus) -> None:
        """Transition a plan to a new PlanStatus, validating the transition."""
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT plan_status FROM apde_plans WHERE id=?", (plan_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Plan {plan_id} not found")
            current = PlanStatus(row["plan_status"])
            if not current.can_transition_to(new_status):
                raise AxiomViolation(
                    f"Plan {plan_id}: invalid status transition "
                    f"{current.value} -> {new_status.value}")
            c.execute(
                "UPDATE apde_plans SET plan_status=? WHERE id=?",
                (new_status.value, plan_id)
            )


# ── DeltaLedger ───────────────────────────────────────────────────────────────

class DeltaLedger:
    """
    Append-only ledger of plan mutations (A2) and execution artifacts.

    Replanning appends a delta row; the base plan_hash is never mutated.

    Fix 4: record_artifact() and get_artifact() persist execution outputs
    (file paths, result blobs, verification outcomes) keyed by (capsule_id, key)
    so ContextWindowManager can populate ContextView.allowed_reads with actual
    data rather than empty stubs.
    """

    def __init__(self, db_path: Path) -> None:
        self._db   = str(db_path)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Create artifact table if it does not yet exist."""
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS apde_plan_deltas (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id     TEXT NOT NULL,
                    seq         INTEGER NOT NULL,
                    delta_type  TEXT NOT NULL,
                    payload     TEXT NOT NULL,
                    ts          REAL NOT NULL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS apde_artifacts (
                    capsule_id  TEXT NOT NULL,
                    key         TEXT NOT NULL,
                    value_json  TEXT NOT NULL,
                    ts          REAL NOT NULL,
                    PRIMARY KEY (capsule_id, key)
                )
            """)

    def _next_seq(self, c: sqlite3.Connection, plan_id: str) -> int:
        """Return the next sequence number for a plan's delta log."""
        row = c.execute(
            "SELECT MAX(seq) as m FROM apde_plan_deltas WHERE plan_id=?",
            (plan_id,)
        ).fetchone()
        return (row["m"] or 0) + 1

    def append(self, plan_id: str, delta_type: str, payload: dict) -> PlanDeltaRow:
        """Append an immutable delta record to the ledger for a plan."""
        with self._lock, self._conn() as c:
            seq = self._next_seq(c, plan_id)
            row = PlanDeltaRow(
                plan_id=plan_id, seq=seq,
                delta_type=delta_type, payload=payload
            )
            c.execute("""
                INSERT INTO apde_plan_deltas (plan_id, seq, delta_type, payload, ts)
                VALUES (?,?,?,?,?)
            """, (plan_id, seq, delta_type, json.dumps(payload), row.ts))
            return row

    def history(self, plan_id: str) -> list[PlanDeltaRow]:
        """Return all delta rows for a plan in sequence order."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM apde_plan_deltas WHERE plan_id=? ORDER BY seq",
                (plan_id,)
            ).fetchall()
        return [PlanDeltaRow(
            plan_id=r["plan_id"], seq=r["seq"],
            delta_type=r["delta_type"], payload=json.loads(r["payload"]),
            ts=r["ts"]
        ) for r in rows]

    def record_artifact(self, capsule_id: str, key: str, value: Any) -> None:
        """
        Persist an execution artifact for a capsule.

        Fix 4: PipelineExecutor calls this after task execution to store
        written file paths, result blobs, and verification outcomes.
        ContextWindowManager reads these when building ContextView._data so
        subsequent tasks receive real prior-task outputs rather than empty dicts.

        Bytes values are base64-encoded so they survive the JSON round-trip.
        On retrieval via get_artifact(), they are automatically decoded back to
        bytes so callers see the original bytes object.

        Args:
            capsule_id: The capsule that produced this artifact.
            key:        A logical name for the artifact (e.g. a file path or
                        task-scoped key like "task_001/result").
            value:      Any JSON-serialisable value, or a bytes object.
        """
        import base64 as _b64
        if isinstance(value, (bytes, bytearray)):
            payload = {"__bytes__": _b64.b64encode(bytes(value)).decode("ascii")}
        else:
            payload = value
        with self._lock, self._conn() as c:
            c.execute("""
                INSERT OR REPLACE INTO apde_artifacts (capsule_id, key, value_json, ts)
                VALUES (?, ?, ?, ?)
            """, (capsule_id, key, json.dumps(payload), time.time()))

    def get_artifact(self, capsule_id: str, key: str) -> Any | None:
        """
        Retrieve a previously recorded artifact.

        Fix 4: Called by ContextWindowManager to populate ContextView._data
        before handing the view to PipelineExecutor so tasks can read outputs
        produced by earlier tasks in the same capsule.

        Bytes artifacts that were stored via record_artifact() are automatically
        decoded from their base64 envelope back to a plain bytes object.

        Returns:
            The deserialized artifact value (bytes for byte blobs), or None if
            no artifact exists for the given (capsule_id, key) pair.
        """
        import base64 as _b64
        with self._conn() as c:
            row = c.execute(
                "SELECT value_json FROM apde_artifacts WHERE capsule_id=? AND key=?",
                (capsule_id, key)
            ).fetchone()
        if row is None:
            return None
        decoded = json.loads(row["value_json"])
        if isinstance(decoded, dict) and "__bytes__" in decoded:
            return _b64.b64decode(decoded["__bytes__"])
        return decoded

    def list_artifacts(self, capsule_id: str) -> dict[str, Any]:
        """
        Return all artifacts for a capsule as a {key: value} dict.

        Used by ContextWindowManager to bulk-load available context data
        into a ContextView before task execution begins.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT key, value_json FROM apde_artifacts WHERE capsule_id=?",
                (capsule_id,)
            ).fetchall()
        return {r["key"]: json.loads(r["value_json"]) for r in rows}
