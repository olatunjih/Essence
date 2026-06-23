"""HeartbeatScheduler +  memory consolidation.
v29.0: evidence-based decay + Analytics Engine delta on knowledge; see wiring doc """
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.retry_queue import get_retry_queue  # noqa: F401
from essence.workspace.scaffold import load_ws_file   # noqa: F401
from essence.memory.memory import Memory              # noqa: F401
from essence.protocols.a2a import A2AClient           # noqa: F401

# HEARTBEAT SCHEDULER
# ══════════════════════════════════════════════════════════════════════════════
# Essence's #1 differentiator vs chatbots: the heartbeat.
# "Every 30 minutes the agent wakes up, reads HEARTBEAT.md, and decides
# whether it needs to do anything." — Essence docs
# We add: HEARTBEAT_OK suppression, cost-saving cheap-check-first pattern,
# direct delivery policy (block | allow), JSONL audit log.

class HeartbeatJob(BaseModel):
    """Pydantic v2 model — serialises cleanly to / from JSON."""
    model_config = ConfigDict(frozen=False)

    name:     str
    message:  str
    schedule: str          # '30m' | '1h' | '1d' | 'cron:0 9 * * *'
    last_run: float = 0.0
    enabled:  bool  = True


def _parse_interval(s: str) -> float | None:
    """Parse an interval string into seconds until next fire.

    Supports:
      • Simple intervals: 30s, 5m, 2h, 1d
      • Full cron expressions (via croniter if installed):
          cron:0 9 * * *       → daily at 09:00
          cron:*/5 * * * *     → every 5 minutes
          cron:0 9 * * 1-5     → weekdays at 09:00
      • Fallback limited cron: cron:MINUTE HOUR * * *  (daily only)

    Returns seconds until next fire, or None if the format is unrecognised.
    """
    s = s.strip()
    # ── Simple interval (30s, 5m, 1h, 1d …) ────────────────────────────────
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|m|h|d)", s.lower())
    if m:
        v, u = float(m.group(1)), m.group(2)
        return v * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u]
    # ── Cron expression ──────────────────────────────────────────────────────
    # Prefix "cron:" is optional (raw 5-field cron strings also accepted).
    expr = re.sub(r"^cron:", "", s, flags=re.I).strip()
    if len(expr.split()) == 5:
        # Try croniter first — supports full cron syntax including */N ranges,
        # weekday/month fields, and lists.  croniter is a tiny pure-Python pkg.
        try:
            from croniter import croniter as _croniter  # type: ignore
            import datetime as _dt
            now = _dt.datetime.now()
            it  = _croniter(expr, now)
            next_dt = it.get_next(_dt.datetime)
            return max(0.0, (next_dt - now).total_seconds())
        except ImportError:
            pass   # croniter not installed — fall through to limited parser
        except Exception as _ce:
            log.debug("cron_parse_error", extra={"expr": expr, "error": str(_ce)[:80]})

        # ── Limited fallback: MINUTE HOUR * * * (daily only) ────────────────
        cm = re.fullmatch(r"(\d+)\s+(\d+)\s+\*\s+\*\s+\*", expr)
        if cm:
            import time as _time
            cron_min, cron_hour = int(cm.group(1)), int(cm.group(2))
            now_st     = _time.localtime()
            target_sod = cron_hour * 3600 + cron_min * 60
            now_sod    = now_st.tm_hour * 3600 + now_st.tm_min * 60 + now_st.tm_sec
            diff = target_sod - now_sod
            if diff <= 0:
                diff += 86400   # already passed today — schedule for tomorrow
            return float(diff)
        log.warning("cron_unsupported_expression",
                    extra={"expr": expr,
                           "hint": "pip install croniter for full cron support"})
    return None


class HeartbeatScheduler:
    """
    Background thread executing HeartbeatJobs at their intervals.
    Jobs are persisted to workspace/heartbeat.json.
    Implements Essence HEARTBEAT_OK suppression to avoid wasted API calls.
    """
    HEARTBEAT_OK = "HEARTBEAT_OK"

    def __init__(self, workspace: Path,
                 run_fn: Callable[[str], str]):
        self._ws   = workspace
        self._run  = run_fn
        self._path = workspace / "heartbeat.json"
        self._jobs: list[HeartbeatJob] = []
        self._lock = threading.Lock()          # guards _jobs list
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._jobs = [HeartbeatJob(**j)
                              for j in json.loads(self._path.read_text(encoding="utf-8"))]
            except Exception: self._jobs = []

    def _save(self) -> None:
        # Must be called with self._lock held
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([j.model_dump() for j in self._jobs], indent=2),
            encoding="utf-8")
        tmp.replace(self._path)

    def add(self, name: str, message: str, schedule: str) -> None:
        with self._lock:
            self._jobs = [j for j in self._jobs if j.name != name]
            self._jobs.append(HeartbeatJob(name=name, message=message,
                                           schedule=schedule))
            self._save()

    def remove(self, name: str) -> bool:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if j.name != name]
            self._save()
            return len(self._jobs) < before

    def list_jobs(self) -> list[HeartbeatJob]:
        with self._lock:
            return list(self._jobs)

    def start(self) -> None:
        if self._thread and self._thread.is_alive(): return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # Default heartbeat: read HEARTBEAT.md every 30 min
        _sleep_agent_ref: Any = None  # set via attach_sleep_agent()
        while not self._stop.is_set():
            now = time.time()
            # System heartbeat
            hb_content = load_ws_file(self._ws, "HEARTBEAT.md")
            if hb_content.strip():
                has_active = any(
                    line.strip()
                    and not line.strip().startswith(("#", "<!--", "-->", "//"))
                    and not line.strip() == "---"
                    for line in hb_content.splitlines()
                )
                if has_active and (now - self.get("__hb_last", 0)) >= 1800:
                    self.set("__hb_last", now)
                    try:
                        result = self._run(hb_content)
                        if result and not result.startswith(self.HEARTBEAT_OK):
                            self._log('__heartbeat__', result)
                        log.info('heartbeat_tick', extra={
                            'suppressed': result.startswith(self.HEARTBEAT_OK)
                            if result else True,
                        })
                    except Exception as exc:
                        log.error('heartbeat_error', extra={'error': str(exc)})

            # Named jobs — copy under lock to avoid mutation during iteration
            with self._lock:
                snapshot = list(self._jobs)
            for job in snapshot:
                if not job.enabled: continue
                interval = _parse_interval(job.schedule)
                if interval and (now - job.last_run) >= interval:
                    with self._lock:
                        job.last_run = now
                        self._save()
                    try:
                        result = self._run(job.message)
                        if result and not result.startswith(self.HEARTBEAT_OK):
                            self._log(job.name, result)
                    except Exception as _exc:
                        log.debug('heartbeat_job_error',
                                  extra={'job': job.name, 'error': str(_exc)})
            # Trigger MetaOrchestrator.on_heartbeat() each tick cycle
            _meta_ref = getattr(self, "_meta_ref", None)
            if _meta_ref is not None:
                try:
                    _meta_ref.on_heartbeat()
                except Exception as _hb_meta_exc:
                    log.debug("meta_orchestrator_heartbeat_error",
                              extra={"error": str(_hb_meta_exc)[:80]})

            # ── Sleep-time compute ──────────────────────────────────────
            # When no conversation has happened for Essence_SLEEP_IDLE_MINS,
            # run the SLEEPTIME_CONSOLIDATOR to rewrite memory with a larger model.
            agent = getattr(self, "_sleep_agent_ref", None)
            if agent is not None:
                idle_mins = int(os.environ.get("Essence_SLEEP_IDLE_MINS", "30"))
                last_chat = getattr(agent, "_last_chat_ts", 0)
                last_sleep = self.get("__sleep_last", 0)
                idle_secs = now - last_chat
                if (idle_secs >= idle_mins * 60
                        and (now - last_sleep) >= idle_mins * 60):
                    self.set("__sleep_last", now)
                    try:
                        self._run_sleep_consolidation(agent)
                    except Exception as _se:
                        log.debug("sleep_consolidation_error",
                                  extra={"error": str(_se)[:120]})
            self._stop.wait(timeout=30)

    def attach_sleep_agent(self, agent: Any) -> None:
        """Attach an Agent instance for sleep-time memory consolidation.
        Call this after the agent is fully initialised."""
        self._sleep_agent_ref = agent

    def attach_meta(self, meta: Any) -> None:
        """Attach a MetaOrchestrator so on_heartbeat() is called each tick.
        Call this after boot_kernel() constructs the orchestrator."""
        self._meta_ref = meta

    def _run_sleep_consolidation(self, agent: Any) -> None:
        """Run the SLEEPTIME_CONSOLIDATOR against current memory state.
        Uses the largest available model (planner tier) for thorough reorganisation."""
        from typing import cast as _cast
        mem = agent.memory
        pool = getattr(agent, "_specialist_pool", {})
        specialist = pool.get(AgentRole.SLEEPTIME_CONSOLIDATOR)
        if specialist is None:
            return

        # Build memory dump: all KV facts + recent episodes
        with mem._kv_lock:
            kv_facts = [f"{k}: {str(v)[:200]}" for k, v in mem._kv.items()]
        episodes = mem.recent_episodes(20)
        ep_texts = [ep["text"][:200] for ep in episodes]
        mem_dump = (
            "=== Semantic KV facts ===\n" + "\n".join(kv_facts[:50]) +
            "\n\n=== Recent episodes ===\n" + "\n".join(ep_texts[:20])
        )
        if not mem_dump.strip():
            return

        raw = specialist.run(mem_dump)
        try:
            clean = re.sub(r"```[a-zA-Z]*", "", raw).strip()
            result = json.loads(clean)
            # Replace KV with deduplicated retain list
            retain = result.get("retain", [])
            if retain and isinstance(retain, list):
                with mem._kv_lock:
                    mem._kv = {f"fact_{i}": f
                                for i, f in enumerate(retain[:100])
                                if isinstance(f, str)}
                mem._save_kv()
                # Re-index semantic backend
                for fact in retain[:100]:
                    mem._backend.store(fact, {"layer": "semantic",
                                               "type": "sleep_reorganised"})
            # Update UserProfile from sleep agent's observations
            profile_updates = result.get("profile", {})
            if profile_updates and isinstance(profile_updates, dict):
                mem.update_profile(profile_updates)

            # v20: Write structured facts to SemanticStateStore
            # The sleep agent can now emit "triples": [{entity, relation, attribute, value}]
            sss = getattr(agent, "_semantic_state", None)
            triples = result.get("triples", [])
            if sss is not None and triples and isinstance(triples, list):
                sss_imported = 0
                for t in triples[:50]:
                    if not isinstance(t, dict): continue
                    try:
                        sss.assert_fact(
                            entity    = str(t.get("entity",    "user")),
                            relation  = str(t.get("relation",  "note")),
                            attribute = str(t.get("attribute", "fact")),
                            value     = str(t.get("value",     ""))[:300],
                            confidence= float(t.get("confidence", 0.8)),
                            source    = "sleep_consolidation",
                        )
                        sss_imported += 1
                    except Exception:
                        pass
                log.debug("sleep_consolidation_sss_imported",
                          extra={"triples": sss_imported})

            log.info("sleep_consolidation_done",
                     extra={"facts_retained": len(retain)})
        except Exception as _pe:
            log.debug("sleep_consolidation_parse_error",
                      extra={"error": str(_pe)[:80]})

    def _log(self, name: str, result: str) -> None:
        log_dir = self._ws / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"heartbeat_{name}.jsonl"
        record   = json.dumps(
            {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "result": result[:1000]})
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(record + "\n")

    def get(self, key: str, default: float = 0.0) -> float:
        # tiny kv for scheduler state
        ks = self._ws / "heartbeat_state.json"
        if ks.exists():
            try: return json.loads(ks.read_text(encoding="utf-8")).get(key, default)
            except Exception: pass
        return default

    def set(self, key: str, value: float) -> None:
        ks = self._ws / "heartbeat_state.json"
        data: dict = {}
        if ks.exists():
            try: data = json.loads(ks.read_text(encoding="utf-8"))
            except Exception: pass
        data[key] = value
        # Atomic write: write to tmp then rename to avoid partial-write corruption
        tmp = ks.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(ks)


# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════

# MEMORY CONSOLIDATION JOB
# ══════════════════════════════════════════════════════════════════════════════
# Runs as a scheduled HeartbeatJob at 03:00 daily. Distils recent episodic
# records into the semantic KV layer, decays stale graph edges, and prunes
# the episodic log to keep disk usage bounded.
#
# This is the "Karpathy sleeptime compute" pattern: do expensive synthesis
# at idle time so retrieval at query time is cheap and precise.

_CONSOLIDATION_SCHEDULE = os.environ.get(
    "Essence_CONSOLIDATE_SCHEDULE", "0 3 * * *")  # daily at 03:00
_CONSOLIDATION_MAX_EPISODES = int(
    os.environ.get("Essence_CONSOLIDATE_MAX_EPISODES", "500"))
_CONSOLIDATION_DECAY_DAYS = float(
    os.environ.get("Essence_CONSOLIDATE_DECAY_DAYS", "30"))


def _consolidation_job_message(workspace: Path) -> str:
    """
    Heartbeat message factory for the memory consolidation job.
    This function is called by HeartbeatScheduler at the scheduled time.
    It performs the actual consolidation and returns a status summary.
    """
    try:
        from pathlib import Path as _Path
        mem = Memory(workspace)
        # 1. Count current episodes
        episodes = mem.recent_episodes(n=_CONSOLIDATION_MAX_EPISODES)
        if not episodes:
            return "Memory consolidation: no episodes to process."
        # 2. Prune old episodes beyond the retention window
        cutoff_ts = time.time() - (_CONSOLIDATION_DECAY_DAYS * 86400)
        recent  = [e for e in episodes if e.get("ts", time.time()) >= cutoff_ts]
        pruned  = len(episodes) - len(recent)
        # 3. Decay graph edges: increment a miss counter stored in KV
        #    Edges not recalled in DECAY_DAYS accumulate misses;
        #    edges with >10 misses are removed.
        graph_keys = [k for k in mem._kv if k.startswith("_graph:")]
        decayed_edges = 0
        for gk in graph_keys:
            edges = list(mem._kv.get(gk, []))
            miss_key = f"_graph_miss:{gk}"
            misses   = int(mem._kv.get(miss_key, 0))
            if misses > 10:
                del mem._kv[gk]
                if miss_key in mem._kv:
                    del mem._kv[miss_key]
                decayed_edges += len(edges)
            else:
                mem._kv[miss_key] = misses + 1
        if decayed_edges or pruned:
            mem._save_kv()
        # 4. Rewrite episodic log with only retained episodes
        if pruned > 0:
            try:
                mem._episodic_path.write_text(
                    "\n".join(json.dumps(e) for e in recent) + "\n",
                    encoding="utf-8")
            except Exception as _e:
                log.debug("consolidation_prune_error",
                          extra={"error": str(_e)[:80]})
        return (f"Memory consolidation complete: {len(recent)} episodes retained, "
                f"{pruned} pruned, {decayed_edges} stale graph edges removed.")
    except Exception as _e:
        log.warning("consolidation_job_error", extra={"error": str(_e)[:120]})
        return f"Memory consolidation error: {_e}"


def _retry_flush_handler(workspace: Path) -> str:
    """Heartbeat sentinel for retry_queue_flush — drains due RetryQueue items."""
    rq = get_retry_queue(workspace)
    if rq is None:
        return "retry_flush: not initialised"
    due = rq.due()
    if not due:
        return f"{HeartbeatScheduler.HEARTBEAT_OK} retry_flush: nothing due"
    flushed = 0
    for item in due:
        try:
            pl  = item["payload"]
            url = pl.get("peer_url", ""); task = pl.get("task", "")
            if url and task:
                client = A2AClient(url); result = client.send_task(task)
                if result and not str(result).startswith("[error"):
                    rq.mark_success(item["id"]); flushed += 1
                else:
                    rq.mark_failure(item["id"], str(result)[:200])
            else:
                rq.mark_failure(item["id"], "missing peer_url or task")
        except Exception as _e:
            rq.mark_failure(item["id"], str(_e)[:200])
    return f"{HeartbeatScheduler.HEARTBEAT_OK} retry_flush: {flushed}/{len(due)}"


def register_consolidation_job(scheduler: "HeartbeatScheduler",
                                workspace: Path) -> None:
    """Register the nightly memory consolidation job with the scheduler."""
    scheduler.add(
        name="memory_consolidation",
        message=f"_consolidation:{workspace}",  # sentinel prefix
        schedule=_CONSOLIDATION_SCHEDULE,
    )
    log.debug("consolidation_job_registered",
              extra={"schedule": _CONSOLIDATION_SCHEDULE})


