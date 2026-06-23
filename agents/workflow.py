"""WorkflowEngine (state machine) +  DAGWorkflowExecutor."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# DAG WORKFLOW EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════
# Replaces the naive sequential partition_parallel_steps() heuristic.
# Each step declares `depends_on: list[int]` (step_ids of prior steps it needs).
# The engine builds a true dependency DAG and automatically parallelises
# everything that has no unsatisfied dependencies.
#
# Compatible with WorkflowState — steps just need a `depends_on` field.
# Falls back to sequential execution when no dependencies are declared.
#
# Usage:
#   dag = DAGWorkflowExecutor(engine)
#   dag.run(state, executor_fn)   # blocks until all steps complete or fail

@_dc.dataclass
class DAGStep:
    """Lightweight wrapper that adds dependency tracking to WorkflowStep."""
    step: "WorkflowStep"
    depends_on: list[int] = _dc.field(default_factory=list)  # step_ids

    @property
    def step_id(self) -> int:
        return self.step.step_id


class DAGWorkflowExecutor:
    """
    Dependency-DAG workflow executor.

    Builds an adjacency list from depends_on declarations and runs all
    steps whose dependencies are satisfied concurrently using a thread pool.
    Respects the existing WorkflowEngine checkpointing and error classification.

    Cycle detection: raises ValueError on circular dependencies at build time.
    """

    def __init__(self, engine: Any,  # WorkflowEngine (forward-declared)
                 max_workers: int = 4) -> None:
        self._engine      = engine
        self._max_workers = max_workers

    @staticmethod
    def _build_dag(dag_steps: list[DAGStep]) -> dict[int, list[int]]:
        """Return {step_id: [step_ids that depend ON it]} — the reverse adjacency list."""
        id_set   = {ds.step_id for ds in dag_steps}
        children: dict[int, list[int]] = {ds.step_id: [] for ds in dag_steps}
        for ds in dag_steps:
            for dep in ds.depends_on:
                if dep not in id_set:
                    raise ValueError(f"Step {ds.step_id} depends_on unknown step {dep}")
                children[dep].append(ds.step_id)
        return children

    @staticmethod
    def _topo_sort(dag_steps: list[DAGStep]) -> list[int]:
        """Kahn's algorithm — returns topological order or raises on cycle."""
        in_degree: dict[int, int] = {ds.step_id: len(ds.depends_on) for ds in dag_steps}
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        order: list[int] = []
        ds_map = {ds.step_id: ds for ds in dag_steps}
        # Build children map inline
        children: dict[int, list[int]] = {ds.step_id: [] for ds in dag_steps}
        for ds in dag_steps:
            for dep in ds.depends_on:
                children[dep].append(ds.step_id)
        while queue:
            node = queue.pop(0)
            order.append(node)
            for child in children.get(node, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)
        if len(order) != len(dag_steps):
            raise ValueError("Cycle detected in workflow DAG — cannot execute")
        return order

    def run(self, state: "WorkflowState",
            executor_fn: Callable[["WorkflowStep"], str],
            replan_fn: Callable[[str, "WorkflowStep"], "WorkflowStep | None"] | None = None,
            ) -> bool:
        """
        Execute all steps in dependency order.
        Steps without dependencies (or whose deps are already complete) run
        concurrently up to max_workers.
        Returns True if all steps succeeded.
        """
        import concurrent.futures as _cf

        # Build DAG steps — use depends_on from step.args if present
        dag_steps = [
            DAGStep(step=s,
                    depends_on=s.args.pop("depends_on", []) if isinstance(s.args, dict) else [])
            for s in state.steps
        ]
        try:
            self._topo_sort(dag_steps)  # validate: cycle check
        except ValueError as _e:
            log.error("dag_cycle_error", extra={"error": str(_e)})
            return False

        id_to_ds     = {ds.step_id: ds for ds in dag_steps}
        completed:  set[int] = set()
        failed:     set[int] = set()
        lock = threading.Lock()
        all_ok = True

        with _cf.ThreadPoolExecutor(max_workers=self._max_workers,
                                     thread_name_prefix="essence-dag") as pool:
            futures: dict[_cf.Future, int] = {}

            def _submit_ready() -> None:
                for ds in dag_steps:
                    sid = ds.step_id
                    if sid in completed or sid in failed:
                        continue
                    already_submitted = any(
                        pool._work_queue.qsize() >= 0  # always True; just check futures map
                        for fid in futures.values() if fid == sid
                    )
                    if any(f_sid == sid for f_sid in futures.values()):
                        continue
                    if all(dep in completed for dep in ds.depends_on):
                        fut = pool.submit(
                            self._engine.execute_step,
                            state, ds.step, executor_fn, replan_fn)
                        futures[fut] = sid

            _submit_ready()

            while futures:
                done, _ = _cf.wait(list(futures), return_when=_cf.FIRST_COMPLETED)
                for fut in done:
                    sid    = futures.pop(fut)
                    status = fut.result()
                    with lock:
                        if status == StepStatus.SUCCESS:
                            completed.add(sid)
                        else:
                            failed.add(sid)
                            all_ok = False
                            # Mark dependents as skipped
                            for other_ds in dag_steps:
                                if sid in other_ds.depends_on:
                                    other_ds.step.status = StepStatus.SKIPPED
                                    failed.add(other_ds.step_id)
                _submit_ready()

        return all_ok

    async def arun(self, state: "WorkflowState",
                  executor_fn: Callable[["WorkflowStep"], Any],
                  replan_fn: Callable[[str, "WorkflowStep"], Any] | None = None,
                  ) -> bool:
        """Async version of run(). Executes ready steps concurrently via asyncio.Task."""
        dag_steps = [
            DAGStep(step=s,
                    depends_on=s.args.pop("depends_on", []) if isinstance(s.args, dict) else [])
            for s in state.steps
        ]
        try:
            self._topo_sort(dag_steps)
        except ValueError as _e:
            log.error("dag_cycle_error", extra={"error": str(_e)})
            return False

        completed: set[int] = set()
        failed:    set[int] = set()
        inflight:  dict[int, asyncio.Task] = {}
        all_ok = True

        while len(completed) + len(failed) < len(dag_steps):
            # 1. Submit ready steps
            for ds in dag_steps:
                sid = ds.step_id
                if sid in completed or sid in failed or sid in inflight:
                    continue
                if all(dep in completed for dep in ds.depends_on):
                    task = asyncio.create_task(
                        self._engine.aexecute_step(state, ds.step, executor_fn, replan_fn))
                    inflight[sid] = task

            if not inflight:
                if len(completed) + len(failed) < len(dag_steps):
                     # Deadlock or logic error
                     log.error("dag_deadlock", extra={"completed": list(completed), "failed": list(failed)})
                     return False
                break

            # 2. Wait for at least one task to finish
            done, _ = await asyncio.wait(list(inflight.values()), return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                # Find which sid finished
                sid = next(s for s, t in inflight.items() if t == task)
                inflight.pop(sid)

                try:
                    status = await task
                    if status == StepStatus.SUCCESS:
                        completed.add(sid)
                    else:
                        failed.add(sid)
                        all_ok = False
                        # Cascade failure: mark dependents as skipped
                        for other_ds in dag_steps:
                            if sid in other_ds.depends_on:
                                other_ds.step.status = StepStatus.SKIPPED
                                failed.add(other_ds.step_id)
                except Exception as _e:
                    log.error("dag_step_exception", extra={"sid": sid, "error": str(_e)})
                    failed.add(sid)
                    all_ok = False

        return all_ok



# WORKFLOW ENGINE
# ══════════════════════════════════════════════════════════════════════════════
# Every task is modelled as a WorkflowState: a sequence of WorkflowSteps each
# with an explicit StepStatus.  Execution is checkpoint-after-commit so a
# process crash resumes from the last completed step, not from scratch.
#
# Key properties:
#   • Idempotent steps — re-running a COMPLETED step returns cached result
#   • Rollback — any step can be rolled back to PENDING for re-execution
#   • Replay — full replay from event log for debugging
#   • Crash-safe — checkpoint written atomically before declaring success

class StepStatus(_enum.Enum):
    PENDING      = "pending"
    RUNNING      = "running"
    SUCCESS      = "success"
    FAILED       = "failed"
    ROLLED_BACK  = "rolled_back"
    SKIPPED      = "skipped"
    RECOVERING   = "recovering"   # mid-recovery attempt before re-execution


@_dc.dataclass
class WorkflowStep:
    step_id:    int
    action:     str
    tool:       str
    args:       dict
    status:     StepStatus      = StepStatus.PENDING
    result:     str             = ""
    retries:    int             = 0
    started_at: float           = 0.0
    ended_at:   float           = 0.0
    failure_cat: str | None     = None
    rollback_data: str          = ""   # JSON snapshot before step ran

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id, "action": self.action,
            "tool": self.tool, "args": self.args,
            "status": self.status.value, "result": self.result,
            "retries": self.retries, "started_at": self.started_at,
            "ended_at": self.ended_at, "failure_cat": self.failure_cat,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorkflowStep":
        s = cls(step_id=d["step_id"], action=d["action"],
                tool=d["tool"], args=d.get("args", {}))
        s.status       = StepStatus(d.get("status", "pending"))
        s.result       = d.get("result", "")
        s.retries      = d.get("retries", 0)
        s.started_at   = d.get("started_at", 0.0)
        s.ended_at     = d.get("ended_at", 0.0)
        s.failure_cat  = d.get("failure_cat")
        return s


@_dc.dataclass
class WorkflowState:
    task_id:      str
    task:         str
    steps:        list[WorkflowStep]
    current_step: int             = 0
    status:       StepStatus      = StepStatus.PENDING
    created_at:   float           = 0.0
    checkpoint_path: Path | None  = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "task": self.task,
            "steps": [s.to_dict() for s in self.steps],
            "current_step": self.current_step,
            "status": self.status.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorkflowState":
        steps = [WorkflowStep.from_dict(s) for s in d.get("steps", [])]
        ws = cls(task_id=d["task_id"], task=d["task"], steps=steps)
        ws.current_step = d.get("current_step", 0)
        ws.status       = StepStatus(d.get("status", "pending"))
        ws.created_at   = d.get("created_at", 0.0)
        return ws


class WorkflowEngine:
    """
    Deterministic workflow execution engine.

    Every task run creates a WorkflowState persisted to disk.
    Execution proceeds step by step; each step is checkpointed on success.
    Crash recovery: reload the state file and call execute() again —
    completed steps return their cached result instantly.
    """

    def __init__(self, workspace: Path) -> None:
        self._ws = workspace / "workflows"
        self._ws.mkdir(parents=True, exist_ok=True)

    def create(self, task: str, raw_steps: list[dict]) -> WorkflowState:
        task_id = f"wf_{int(time.time())}_{secrets.token_hex(4)}"
        steps   = [
            WorkflowStep(
                step_id = s.get("step", i + 1),
                action  = s.get("action", ""),
                tool    = s.get("tool", "none"),
                args    = s.get("args", {}),
            )
            for i, s in enumerate(raw_steps)
        ]
        state = WorkflowState(
            task_id=task_id, task=task, steps=steps,
            created_at=time.time(),
            checkpoint_path=self._ws / f"{task_id}.json",
        )
        self._checkpoint(state)
        return state

    def _checkpoint(self, state: WorkflowState) -> None:
        """Write state atomically: write tmp → rename."""
        if state.checkpoint_path is None:
            return
        tmp = state.checkpoint_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.to_dict(), indent=2, default=str),
                       encoding="utf-8")
        tmp.replace(state.checkpoint_path)

    # ── Workspace snapshot (pre-destructive-tool safety net) ────────────────
    _DESTRUCTIVE_TOOLS = frozenset({
        "shell", "write_file", "python_exec", "finetune",
        "train_model", "build_skill",
    })

    def snapshot_workspace(self, state: "WorkflowState",
                           step: "WorkflowStep",
                           workspace: Path) -> Path | None:
        """
        Create a lightweight workspace snapshot before a destructive tool runs.
        Returns the snapshot path, or None if not needed or snapshotting fails.
        """
        if step.tool not in self._DESTRUCTIVE_TOOLS:
            return None
        snap_path = self._ws / f"{state.task_id}_snap_{step.step_id}.tar.gz"
        try:
            import tarfile
            # v20: Smart file selection — exclude logs, sort by recency+size,
            # make limit configurable. Logs used to fill the quota before source
            # files under heavy workloads (rglob[:500] was order-dependent).
            _snap_limit = int(os.environ.get("Essence_SNAP_FILES", "500"))
            _EXCLUDE_PATTERNS = frozenset({
                ".log", ".tmp", ".pyc", ".pyo", ".o", ".so",
            })
            _EXCLUDE_DIRS = frozenset({"logs", "__pycache__", ".git", "node_modules"})

            def _file_priority(p: Path) -> tuple:
                # Higher = more important: recency (mtime) × log(size+1)
                import math
                try:
                    st = p.stat()
                    return (st.st_mtime, math.log(st.st_size + 1))
                except OSError:
                    return (0.0, 0.0)

            candidates = [
                p for p in workspace.rglob("*")
                if p.is_file()
                and p.suffix not in _EXCLUDE_PATTERNS
                and not any(part.startswith(".") for part in p.parts[-3:])
                and not any(part in _EXCLUDE_DIRS for part in p.relative_to(workspace).parts)
            ]
            # Sort descending by (mtime, log_size) so newest+largest source files win
            candidates.sort(key=_file_priority, reverse=True)
            with tarfile.open(snap_path, "w:gz") as tf:
                for p in candidates[:_snap_limit]:
                    tf.add(p, arcname=str(p.relative_to(workspace)))
            log.debug("workspace_snapshot_created",
                      extra={"task_id": state.task_id, "step_id": step.step_id,
                             "path": str(snap_path)})
            return snap_path
        except Exception as _e:
            log.debug("workspace_snapshot_error", extra={"error": str(_e)[:80]})
            return None

    def restore_snapshot(self, snap_path: Path, workspace: Path) -> bool:
        """Restore a workspace snapshot. Returns True on success."""
        try:
            import tarfile
            with tarfile.open(snap_path, "r:gz") as tf:
                tf.extractall(workspace)
            log.info("workspace_snapshot_restored",
                     extra={"snap": str(snap_path)})
            return True
        except Exception as _e:
            log.warning("workspace_snapshot_restore_error",
                        extra={"error": str(_e)[:120]})
            return False

    def classify_error(self, error: str) -> str:
        """
        Classify a step error into a recovery category.

        transient   → retry with exponential backoff (network, timeout)
        recoverable → replan with constraints derived from the failure context
        fatal       → escalate to human via DecisionQueue
        """
        err_lower = error.lower()
        TRANSIENT_SIGNALS = (
            "timeout", "connection", "network", "refused", "temporarily",
            "rate limit", "429", "503", "retry",
        )
        FATAL_SIGNALS = (
            "permission denied", "access denied", "unauthorized",
            "no such file", "disk full", "quota exceeded",
            "killed", "segmentation fault",
        )
        if any(s in err_lower for s in FATAL_SIGNALS):
            return "fatal"
        if any(s in err_lower for s in TRANSIENT_SIGNALS):
            return "transient"
        return "recoverable"

    def resume(self, task_id: str) -> WorkflowState | None:
        """Load a previous workflow from disk for crash recovery / replay."""
        path = self._ws / f"{task_id}.json"
        if not path.exists():
            return None
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            state = WorkflowState.from_dict(d)
            state.checkpoint_path = path
            return state
        except Exception:
            return None

    def execute_step(self, state: WorkflowState, step: WorkflowStep,
                     executor_fn: Callable[[WorkflowStep], str],
                     replan_fn: Callable[[str, WorkflowStep], WorkflowStep | None] | None = None,
                     ) -> StepStatus:
        """
        Execute one step using executor_fn.
        • If step is already SUCCESS, return cached result immediately (idempotent).
        • Transient failures → exponential back-off retry (up to max_retries).
        • Recoverable failures → call replan_fn (if provided) to mutate the step
          args/action before the next attempt, instead of retrying blindly.
        • Fatal failures → mark FAILED immediately; escalate via DecisionQueue.
        • Checkpoint after every status change.

        v20: replan_fn signature: (failure_message, step) → mutated_step | None
        When None is returned the engine falls back to normal retry.
        """
        if step.status == StepStatus.SUCCESS:
            return StepStatus.SUCCESS   # idempotent — return cached

        step.status     = StepStatus.RUNNING
        step.started_at = time.time()
        self._checkpoint(state)

        max_retries = 3
        last_exc: Exception | None = None

        for attempt in range(max_retries):
            try:
                step.result  = executor_fn(step)
                step.status  = StepStatus.SUCCESS
                step.ended_at = time.time()
                self._checkpoint(state)
                return StepStatus.SUCCESS
            except Exception as e:
                last_exc       = e
                step.retries   = attempt + 1
                err_msg        = str(e)
                err_cat        = self.classify_error(err_msg)
                step.failure_cat = err_cat
                log.warning("workflow_step_failed",
                            extra={"step_id": step.step_id, "attempt": attempt,
                                   "category": err_cat, "error": err_msg})

                if err_cat == "fatal":
                    # Fatal — stop immediately; let the caller escalate
                    break

                if err_cat == "recoverable" and replan_fn and attempt < max_retries - 1:
                    # v20: Pass failure context to a Planner-backed replan_fn
                    # instead of retrying the same args verbatim.
                    step.status = StepStatus.RECOVERING
                    self._checkpoint(state)
                    try:
                        mutated = replan_fn(err_msg, step)
                        if mutated is not None:
                            step.action = mutated.action
                            step.tool   = mutated.tool
                            step.args   = mutated.args
                            log.info("workflow_step_replanned",
                                     extra={"step_id": step.step_id,
                                            "new_tool": step.tool})
                    except Exception as _re:
                        log.debug("workflow_step_replan_error",
                                  extra={"error": str(_re)[:120]})
                    step.status = StepStatus.RUNNING
                    self._checkpoint(state)
                    # Don't sleep after a replan — we already spent time replanning
                    continue

                if attempt < max_retries - 1:
                    import random as _rj
                    time.sleep(2 ** attempt + _rj.uniform(0, 0.5))

        step.status   = StepStatus.FAILED
        step.result   = f"[FAILED after {max_retries} attempts ({step.failure_cat}): {last_exc}]"
        step.ended_at = time.time()
        self._checkpoint(state)
        return StepStatus.FAILED

    async def aexecute_step(self, state: WorkflowState, step: WorkflowStep,
                           executor_fn: Callable[[WorkflowStep], Any], # can be coroutine
                           replan_fn: Callable[[str, WorkflowStep], Any] | None = None,
                           ) -> StepStatus:
        """Async version of execute_step(). Handles coroutine executors."""
        if step.status == StepStatus.SUCCESS:
            return StepStatus.SUCCESS

        step.status     = StepStatus.RUNNING
        step.started_at = time.time()
        self._checkpoint(state)

        max_retries = 3
        last_exc: Exception | None = None

        for attempt in range(max_retries):
            try:
                res = executor_fn(step)
                if asyncio.iscoroutine(res):
                    step.result = await res
                else:
                    step.result = res

                step.status  = StepStatus.SUCCESS
                step.ended_at = time.time()
                self._checkpoint(state)
                return StepStatus.SUCCESS
            except Exception as e:
                last_exc       = e
                step.retries   = attempt + 1
                err_msg        = str(e)
                err_cat        = self.classify_error(err_msg)
                step.failure_cat = err_cat

                if err_cat == "fatal":
                    break

                if err_cat == "recoverable" and replan_fn and attempt < max_retries - 1:
                    step.status = StepStatus.RECOVERING
                    self._checkpoint(state)
                    try:
                        mut_res = replan_fn(err_msg, step)
                        mutated = await mut_res if asyncio.iscoroutine(mut_res) else mut_res
                        if mutated is not None:
                            step.action = mutated.action
                            step.tool   = mutated.tool
                            step.args   = mutated.args
                    except Exception as _re:
                        log.debug("workflow_step_replan_error", extra={"error": str(_re)[:120]})
                    step.status = StepStatus.RUNNING
                    self._checkpoint(state)
                    continue

                if attempt < max_retries - 1:
                    import random as _rj
                    await asyncio.sleep(2 ** attempt + _rj.uniform(0, 0.5))

        step.status   = StepStatus.FAILED
        step.result   = f"[FAILED after {max_retries} attempts ({step.failure_cat}): {last_exc}]"
        step.ended_at = time.time()
        self._checkpoint(state)
        return StepStatus.FAILED

    def rollback_step(self, state: WorkflowState,
                      step_idx: int) -> None:
        """Mark step and all subsequent steps ROLLED_BACK → PENDING for re-execution."""
        for i in range(step_idx, len(state.steps)):
            s = state.steps[i]
            # Roll back any step that is not still in its initial PENDING state
            # and also force PENDING steps to ROLLED_BACK so the whole tail
            # is uniformly marked for re-execution.
            s.status = StepStatus.ROLLED_BACK
        self._checkpoint(state)

    def list_workflows(self) -> list[str]:
        return [p.stem for p in sorted(self._ws.glob("wf_*.json"),
                                       key=lambda p: p.stat().st_mtime,
                                       reverse=True)[:50]]


# ══════════════════════════════════════════════════════════════════════════════
