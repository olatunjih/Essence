"""Agent: production agent class with ReAct + Analytics Engine integration."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

from essence.agents.config import AgentConfig   # noqa: F401
from essence.agents.intent import build_task_spec, TaskSpec  # noqa: F401  [real source bug: used in run_task() without import]
from essence.agents.critic import RequestComplexity, CriticResult  # noqa: F401
from essence.workspace.scaffold import _DEFAULT_SOUL, _DEFAULT_TOOLS, _DEFAULT_HEARTBEAT  # noqa: F401
from essence.analytics.spine import get_analytical_spine, AnalyticalStateBus  # noqa: F401
from essence.agents.specialist import AgentRole  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.critic import _classify_complexity  # noqa: F401  [auto-fix: runtime NameError]
from essence.infra.token_count import count_tokens  # noqa: F401  [auto-fix: runtime NameError]
from essence.workspace.skill_system import skills_summary  # noqa: F401  [auto-fix: runtime NameError]
from essence.tools.registry import TOOL_REGISTRY  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.alignment import NeuromorphicEventBus  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.alignment import ValueAlignmentOracle  # noqa: F401  [auto-fix: runtime NameError]
from essence.analytics.models import DomainLens  # noqa: F401  [auto-fix: runtime NameError]
from essence.analytics.domain_lens import register_default_lenses  # noqa: F401  [auto-fix: runtime NameError]
from essence.analytics.domain_lens import DomainLensManager  # noqa: F401  [auto-fix: runtime NameError]
from essence.analytics.learning import LearningEngine  # noqa: F401  [auto-fix: runtime NameError]
from essence.analytics.resilience import ResilienceLayer  # noqa: F401  [auto-fix: runtime NameError]
from essence.analytics.engine import WaveController  # noqa: F401  [auto-fix: runtime NameError]
from essence.analytics.engine import AnalyticalCore  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.verifier import CognitiveReflector  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.tot import TreeOfThought  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.adaptation import WorkflowCompressor  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.critic import _CRITIC_GATE_SYS  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.adaptation import PromptEvolution  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.adaptation import ReasoningScratchpad  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.adaptation import SharedBlackboard  # noqa: F401  [auto-fix: runtime NameError]
from essence.memory.team_sync import TeamMemorySync  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.workflow import DAGWorkflowExecutor  # noqa: F401  [auto-fix: runtime NameError]
from essence.backends.routing import ContextualBanditRouter  # noqa: F401  [auto-fix: runtime NameError]
from essence.backends.routing import ABModelRouter  # noqa: F401  [auto-fix: runtime NameError]
from essence.backends.routing import ContextBudgetManager  # noqa: F401  [auto-fix: runtime NameError]
from essence.memory.semantic_state import SemanticStateStore  # noqa: F401  [auto-fix: runtime NameError]
from essence.protocols.protocol_kernel import (               # noqa: F401
    ConflictResolution, MessageProtocol, TaskHandoff,
)
from essence.agents.proactive import ProactiveEngine  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.verifier import VerifierLayer  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.observer import AgentObserver  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.decision import DecisionQueue  # noqa: F401  [auto-fix: runtime NameError]
from essence.security.tokens import CapabilityPolicy  # noqa: F401  [auto-fix: runtime NameError]
from essence.workspace.scaffold import load_ws_file  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.workflow import WorkflowEngine  # noqa: F401  [auto-fix: runtime NameError]
from essence.workspace.sop import SOPLoader  # noqa: F401  [auto-fix: runtime NameError]
from essence.backends.routing import CostTracker  # noqa: F401  [auto-fix: runtime NameError]
from essence.workspace.ingestor import DocumentIngestor  # noqa: F401  [auto-fix: runtime NameError]
from essence.analytics.experiment import ExperimentTracker  # noqa: F401  [auto-fix: runtime NameError]
from essence.agents.critic import _synthesise_constraints  # noqa: F401  [auto-fix: runtime NameError]
from essence.memory.memory import Memory  # noqa: F401  [auto-fix: runtime NameError]
# AGENT CORE  (Nemotron + TaskPipeline + task management design)
# ══════════════════════════════════════════════════════════════════════════════
# Architecture:
#   Planner   (thinking ON)   — decomposes task to JSON step plan
#   Executor                  — dispatches tools or LLM
#   Critic    (CriticGate)  — validates against constraints; one retry
#
# "Thinking tax" mitigation from Nemotron 3 Super design:
#   → thinking ON only for Planner; OFF for Executor (saves tokens on subtasks)
#   → multi-agent systems generate 15× tokens of standard chat; don't reason
#     at every subtask
#
# Memory distillation fires at `memory_window` turns to stay inside ctx window.
# SOUL.md + IDENTITY.md + MEMORY.md + skills injected into every system prompt.

_PLAN_SYS = (
    "You are a precise task planner. Break the user's request into numbered steps. "
    "Output ONLY a JSON array (no markdown, no prose): "
    '[{"step":1,"action":"...","tool":"shell|read_file|write_file|'
    'python_exec|web_search|heartbeat_add|none","args":{}}]. '
    "No other text."
)
_EXEC_SYS = (
    "You are a precise executor. Complete the given action using tools when appropriate. "
    'Emit a JSON tool call {"tool":"name","args":{...}} or completion '
    '{"done":true,"result":"..."}. No other text outside JSON.'
)
_MEM_SYS = (
    "Distil the essential facts from this conversation into ≤400 tokens. "
    "Focus on facts needed to continue the task. Plain text only."
)



# ══════════════════════════════════════════════════════════════════════════════
# AGENT CORE (AgentConfig + Agent)
# ══════════════════════════════════════════════════════════════════════════════
# Steps marked independent by the planner run concurrently via asyncio.gather()
# with a configurable concurrency cap.  Deps-linked and destructive tool steps
# always execute sequentially.

async def _execute_steps_parallel(
    steps: "list[WorkflowStep]",
    executor_fn: "Callable[[WorkflowStep], str]",
    max_concurrency: int = 4,
    log_fn: "Callable[[str], None]" = print,
) -> "list[tuple[WorkflowStep, str]]":
    """
    Execute a batch of independent workflow steps concurrently.

    Args:
        steps:           List of WorkflowStep objects to run in parallel.
        executor_fn:     Sync function mapping WorkflowStep → result string.
                         Runs in a thread-pool executor to avoid blocking.
        max_concurrency: Maximum simultaneous steps (default 4).
        log_fn:          Progress logger.

    Returns:
        List of (step, result) tuples sorted by original step_id.
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    loop      = asyncio.get_running_loop()
    results: list[tuple[int, "WorkflowStep", str]] = []

    async def _run_one(ws: "WorkflowStep") -> tuple[int, "WorkflowStep", str]:
        async with semaphore:
            log_fn(f"  {cyan(f'→ Step {ws.step_id} [parallel]')}: {ws.action[:60]}")
            result = await loop.run_in_executor(None, executor_fn, ws)
            return ws.step_id, ws, result

    tasks = [asyncio.create_task(_run_one(s)) for s in steps]
    for coro in asyncio.as_completed(tasks):
        sid, ws, result = await coro
        results.append((sid, ws, result))
        log_fn(f"  {green(f'  ✓ Step {sid}')} → {str(result)[:80]}")

    results.sort(key=lambda x: x[0])
    return [(ws, res) for _, ws, res in results]


def partition_parallel_steps(steps: "list[WorkflowStep]") -> "list[list[WorkflowStep]]":
    """
    Partition a workflow step list into sequential batches.
    Each batch contains steps that can run in parallel.

    A step is considered sequential (must run alone) if:
      - Its tool is in ALWAYS_SEQUENTIAL_TOOLS
      - Its args contain a reference to a prior step's output
      - It is explicitly marked sequential in planner output

    Returns a list of batches; each batch is a list of parallel-safe steps.
    Caller runs each batch with _execute_steps_parallel, then moves to next batch.
    """
    ALWAYS_SEQUENTIAL_TOOLS = {
        "write_file", "shell", "python_exec", "finetune",
        "train_model", "speech", "build_skill",
    }
    batches: list[list["WorkflowStep"]] = []
    current_batch: list["WorkflowStep"] = []

    for step in steps:
        # Check if this step must run alone
        args_json = json.dumps(step.args)
        is_seq = (
            step.tool in ALWAYS_SEQUENTIAL_TOOLS or
            step.args.get("sequential") is True or
            # References a prior step's output placeholder
            any(f"step_{i}" in args_json or f"result_{i}" in args_json
                for i in range(step.step_id))
        )
        if is_seq:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
            batches.append([step])
        else:
            current_batch.append(step)

    if current_batch:
        batches.append(current_batch)

    return batches



# Resolve forward references now that all types are defined
try:
    AgentConfig.model_rebuild()
except Exception:
    pass


class Agent:
    """
    Production-grade agent session.

    New in v14-v19:
    • WorkflowEngine  — deterministic state machine, crash recovery, idempotent replay
    • SpecialistPool  — role-based model routing (Planner/Executor/Critic at right size)
    • ComplexityRouter — cheapest-capable model per step
    • CapabilityPolicy — pre-call token grant/deny (replaces binary y/N)
    • DecisionQueue   — async human-in-the-loop approval queue
    • AgentObserver   — token tracing, tool latency, behavioral drift
    • VerifierLayer   — hallucination cross-check against tool results
    • ProactiveEngine — surfaces stale projects, disk alerts, overdue tasks
    • Three-layer Memory — working / episodic / semantic consolidation

    New in v20 (Phase 3):
    • SemanticStateStore — entity-relation-attribute triples with conflict detection;
      injected into every system prompt; written by consolidation + sleep agent
    • ContextBudgetManager — unified token allocator across all context components;
      priority eviction prevents quality degradation on T0/T1 devices
    • ContextualBanditRouter (LinUCB) — replaces weighted-random A/B with contextual
      signals (complexity, latency, time-of-day); cold-start falls back to A/B
    • DAGWorkflowExecutor — true dependency DAG with concurrent execution;
      failed steps auto-skip dependents; cycle detection at build time
    • _make_replan_fn() — Planner-backed recoverable error replanning;
      generates alternative step approach instead of blind retry
    • TeamMemorySync — rsync-like differential push/pull over A2A channels;
      private facts never leave node; background daemon thread
    • use_skill tool — skills can invoke other skills at runtime;
      per-thread cycle detection via _skill_call_stack
    • Streaming tool results → WebhookEventBus — every tool dispatch emits
      a live progress event; web UI and TUI get real-time updates
    • Workflow DAG visual editor at /workflow-editor — SVG step graph with
      click-to-inspect, status colours, per-step result viewer
    """
    def __init__(self, cfg: AgentConfig,
                 soul: str = _DEFAULT_SOUL,
                 identity: str = "",
                 tools_md: str = _DEFAULT_TOOLS,
                 skills: dict[str, str] | None = None,
                 memory: Memory | None = None,
                 hw: "HardwareProfile | None" = None,
                 scheduler: "HeartbeatScheduler | None" = None,
                 bridge: "SystemBridge | None" = None):
        self.cfg         = cfg
        self.soul        = soul
        self.identity    = identity
        self.tools_md    = tools_md
        self.skills      = skills or {}
        # Shared memory: Essence_SHARED_MEMORY_PATH lets multiple Essence nodes
        # (ORCHESTRATOR + WORKERs) point their semantic backend at a common
        # directory while keeping per-node working/episodic layers local.
        _shared_mem = os.environ.get("Essence_SHARED_MEMORY_PATH", "")
        _mem_root   = Path(_shared_mem) if _shared_mem else cfg.workspace
        self.memory      = memory or Memory(_mem_root, tier=getattr(hw,'tier',0))
        self.memory._agent_ref = self   # enables LLM extraction in _auto_consolidate
        self.constraints = _synthesise_constraints(tools_md)
        self.history: list[dict] = []
        self._distilled  = ""
        self._session_id = f"s{int(time.time())}_{id(self) & 0xFFFF:04x}"
        self._hw         = hw
        self._scheduler  = scheduler
        self._tracker    = ExperimentTracker(cfg.workspace)
        self._ingestor   = DocumentIngestor(self.memory, cfg.workspace)
        self._bridge     = bridge
        # v18: cost tracking + SOP loading
        self._cost       = CostTracker(cfg.workspace, budget=cfg.cost_budget)
        self._sop        = SOPLoader(cfg.sop_dir or None)
        self._workflow_engine = WorkflowEngine(cfg.workspace)
        # Structured identity files — cached at init, reloaded on demand via
        # reload_identity_files() so _sys() never hits the filesystem per-turn.
        self._goals    = load_ws_file(cfg.workspace, "GOALS.md", "")
        self._projects = load_ws_file(cfg.workspace, "PROJECTS.md", "")
        self._learned  = load_ws_file(cfg.workspace, "LEARNED.md", "")

        # ── Production systems ─────────────────────────────────────────────
        self._workflow_engine = WorkflowEngine(cfg.workspace)
        self._cap_policy      = CapabilityPolicy(
            autonomy_level=cfg.autonomy_level, ttl_s=60.0)
        self._decision_queue  = DecisionQueue(cfg.workspace)
        self._observer        = AgentObserver(cfg.workspace, self._session_id)
        self._specialist_pool: dict[AgentRole, SpecialistAgent] = (
            build_specialist_pool(hw, cfg.provider) if hw else {})
        self._verifier        = VerifierLayer(
            provider=cfg.provider,
            model=route_model_for_complexity(
                hw, RequestComplexity.TRIVIAL) if hw else cfg.model,
            enabled=True)
        self._proactive       = ProactiveEngine(cfg.workspace, self.memory)

        # ── v20: v20 systems ─────────────────────────────────────────────
        # SemanticStateStore — structured entity-relation-attribute beliefs
        self._semantic_state = SemanticStateStore(
            cfg.workspace / "memory" / "semantic_state.json")

        # ContextBudgetManager — token allocation across context components
        # Auto-sizes to model context window; T0 default=4096, T1=8192, T2+=32768
        _ctx_window = {0: 4096, 1: 8192, 2: 32768, 3: 131072}.get(
            getattr(hw, "tier", 0), 8192)
        self._ctx_budget = ContextBudgetManager(context_window=_ctx_window)

        # ContextualBanditRouter — replaces weighted-random A/B routing
        _ab_router = getattr(cfg.provider, "_ab_router",
                             ABModelRouter(cfg.workspace))
        self._bandit = ContextualBanditRouter(cfg.workspace, _ab_router)

        # DAGWorkflowExecutor — dependency-aware parallel step execution
        self._dag_executor = DAGWorkflowExecutor(
            self._workflow_engine,
            max_workers=min(4, os.cpu_count() or 2))

        # TeamMemorySync — background differential push/pull
        _peer_urls = [u.strip() for u in
                      os.environ.get("Essence_A2A_PEERS", "").split(",") if u.strip()]
        self._team_sync = TeamMemorySync(
            self._semantic_state, _peer_urls, namespace=_TEAM_ID)
        self._team_sync.start()   # no-op unless Essence_TEAM_SYNC=1 + peers set
        # ── v28.1: Architecture gap-fill components ───────────────────────────
        # SharedBlackboard — multi-agent collaborative workspace
        self._blackboard = SharedBlackboard()
        # ReasoningScratchpad — chain-of-thought trace buffer
        self._scratchpad = ReasoningScratchpad(max_entries=50)
        # PromptEvolution — UCB1-selected prompt variants
        self._prompt_evolver = PromptEvolution(cfg.workspace)
        self._prompt_evolver.seed("planner", _PLAN_SYS)
        self._prompt_evolver.seed("executor", _EXEC_SYS)
        self._prompt_evolver.seed("critic", _CRITIC_GATE_SYS)
        # WorkflowCompressor — repeated pattern → skill
        self._wf_compressor = WorkflowCompressor(cfg.workspace)
        # TreeOfThought — multi-path plan exploration
        self._tot = TreeOfThought(cfg.provider, cfg.model)
        self._reflector = CognitiveReflector(cfg.provider, cfg.model)

        # ── v29: Analytics Engine Analytical Intelligence Architecture ───────────────────
        self._prism_core = AnalyticalCore()
        self._wave_controller = WaveController(self._prism_core)
        self._aegis = ResilienceLayer()
        self._genesis = LearningEngine(cfg.workspace)
        self._nexus = DomainLensManager()
        register_default_lenses(self._nexus)  # v29 bugfix: nexus had zero lenses registered,
                                               # so auto_detect_domain() could never match anything
        self._analytical_spine = AnalyticalStateBus()
        # Register this instance as the process-wide spine singleton so the API
        # (/api/status, /api/prism/findings) reads the same object the live agent
        # is writing to — fixes the Agent↔API spine split.
        try:
            from essence.analytics.layers import set_analytical_spine
            set_analytical_spine(self._analytical_spine)
        except Exception:
            pass
        # ──  Protocol Kernel — typed multi-agent message fabric ─────────────
        self._msg_protocol      = MessageProtocol(version="1.0")
        self._task_handoff      = TaskHandoff()
        self._conflict_resolver = ConflictResolution()
        # v30: Advanced Cognitive Features
        self._alignment_oracle = ValueAlignmentOracle(cfg.provider, cfg.model)
        self._event_bus = NeuromorphicEventBus()
        # v29 bugfix: NeuromorphicEventBus was previously instantiated
        # but never wired to any subscriber — dead code. Connect it to
        # the observer and proactive engine so lifecycle events actually
        # propagate.
        if hasattr(self._observer, "_on_task_start"):
            self._event_bus.subscribe("task_start", self._observer._on_task_start)
        if hasattr(self._observer, "_on_task_end"):
            self._event_bus.subscribe("task_end",   self._observer._on_task_end)
        if hasattr(self._observer, "_on_tool_call"):
            self._event_bus.subscribe("tool_call",  self._observer._on_tool_call)
        if hasattr(self._proactive, "_on_event"):
            self._event_bus.subscribe("proactive",  self._proactive._on_event)

        # Wire context-sensitive tool handlers into TOOL_REGISTRY
        self._register_tool_handlers()

        # ──  Analytical components (critic / verifier / reward / evolver) ─
        # Previously _build_analytical_components() was defined but never called
        # from __init__, leaving critic gating, reward scoring, and prompt
        # evolution permanently dormant.
        try:
            _comps = _build_analytical_components(
                self._analytical_spine,
                provider=cfg.provider,
                model=cfg.model,
                base_prompt_evol=self._prompt_evolver,
            )
            self._analytical_critic   = _comps["critic"]
            self._analytical_verifier = _comps["verifier"]
            self._delayed_queue       = _comps["delayed_queue"]
            self._analytical_evolver  = _comps["evolver"]
        except Exception:
            self._analytical_critic   = None
            self._analytical_verifier = None
            self._delayed_queue       = None
            self._analytical_evolver  = None

    # ── v20: Planner-backed replan factory ─────────────────────────────────
    def _make_replan_fn(self):
        """
        Return a replan_fn(error_msg, step) → WorkflowStep | None for
        WorkflowEngine.execute_step().  Uses the PLANNER specialist to
        generate an alternative step approach when recoverable errors occur.
        Falls back to None (no replan) when no planner is available.
        """
        planner = self._specialist_pool.get(AgentRole.PLANNER) if self._specialist_pool else None
        if planner is None:
            return None

        def _replan(error_msg: str, step: "WorkflowStep") -> "WorkflowStep | None":
            try:
                replan_prompt = (
                    f"A workflow step failed with a recoverable error.\n"
                    f"Original action: {step.action}\n"
                    f"Original tool:   {step.tool}\n"
                    f"Original args:   {json.dumps(step.args)[:300]}\n"
                    f"Error:           {error_msg[:300]}\n\n"
                    "Suggest an alternative approach.  "
                    'Return ONLY valid JSON: {"action": "...", "tool": "...", "args": {...}}\n'
                    "Choose a different tool or different args that avoid the error."
                )
                raw = planner.run(replan_prompt)
                clean = re.sub(r"```[a-zA-Z]*", "", raw).strip()
                patch = json.loads(clean)
                if isinstance(patch, dict) and "tool" in patch:
                    step.action = patch.get("action", step.action)
                    step.tool   = patch.get("tool",   step.tool)
                    step.args   = patch.get("args",   step.args)
                    log.info("workflow_step_replanned_by_planner",
                             extra={"step_id": step.step_id,
                                    "new_tool": step.tool})
                    return step
            except Exception as _e:
                log.debug("replan_fn_failed", extra={"error": str(_e)[:120]})
            return None

        return _replan

    # ── Build full system prompt ────────────────────────────────────────────
    def reload_identity_files(self) -> None:
        """Re-read GOALS.md, PROJECTS.md, LEARNED.md from disk.
        Automatically called by skill_write after hot-reloading a new skill."""
        ws = self.cfg.workspace
        self._goals    = load_ws_file(ws, "GOALS.md", "")
        self._projects = load_ws_file(ws, "PROJECTS.md", "")
        self._learned  = load_ws_file(ws, "LEARNED.md", "")
        # Invalidate the stable-parts cache so _sys() rebuilds on next call
        self._sys_stable_cache: str | None = None

    def _sys(self, extra: str = "", user_query: str = "") -> str:
        # Stable parts (soul, identity, constraints, skills) are cached between
        # turns and only rebuilt when reload_identity_files() is called.
        # Dynamic parts (memory recall, proactive events, SSS block) recompute
        # each turn. This cuts _sys() from ~10 ops/call to ~4 on cache hit.
        if not hasattr(self, "_sys_stable_cache"):
            self._sys_stable_cache = None
        parts = [self.soul]
        if self.identity:
            parts.append(f"[User Identity]\n{self.identity}")
        # Structured identity files — use cached copies (set in __init__ /
        # refreshed by reload_identity_files) to avoid per-turn disk reads.
        if self._goals.strip():
            parts.append(f"[Goals]\n{self._goals.strip()}")
        if self._projects.strip():
            parts.append(f"[Active Projects]\n{self._projects.strip()}")
        if self._learned.strip():
            parts.append(f"[Learned]\n{self._learned.strip()}")
        if self._distilled:
            parts.append(f"[Prior context]\n{self._distilled}")
        mem_ctx = self.memory.facts()
        if mem_ctx:
            parts.append(mem_ctx)
        # ── Multi-layer RAG recall ────────────────────────────────────────
        if user_query:
            layers = self.memory.recall(user_query, k=4)
            rag_parts = []
            for layer_name, hits in layers.items():
                if hits:
                    rag_parts.append(
                        f"[{layer_name.capitalize()} memory]\n" +
                        "\n---\n".join(h[:300] for h in hits[:3]))
            if rag_parts:
                parts.append("\n\n".join(rag_parts))
        sk = skills_summary(self.skills)
        if sk:
            parts.append(sk)
        ingested = self.memory.get("_ingested_docs", {})
        if ingested:
            parts.append("[Ingested documents]\n" +
                         "\n".join(f"  {k}: {v} chunks"
                                   for k, v in list(ingested.items())[:10]))
        if self.constraints:
            parts.append("[Tool Constraints]\n" +
                         "\n".join(f"  - {c}" for c in self.constraints))
        # ── Proactive briefing (surface stale projects / alerts) ─────────
        proactive_events = self._proactive.scan()
        if proactive_events:
            briefing = self._proactive.format_briefing(proactive_events[:3])
            if briefing:
                parts.append(briefing)
        if extra:
            parts.append(extra)

        # v20: Inject SemanticStateStore block (structured beliefs)
        if hasattr(self, "_semantic_state"):
            sss_block = self._semantic_state.to_prompt_block(max_facts=30)
            if sss_block:
                parts.append(sss_block)

        # Inject user preference hint (response format / communication style)
        if hasattr(self, "_kernel") and getattr(self._kernel, "_s", None) is not None:
            _upe = getattr(self._kernel._s, "user_preference_engine", None)
            if _upe is not None:
                try:
                    _pref_hint = _upe.system_hint()
                    if _pref_hint:
                        parts.append(_pref_hint)
                except Exception:
                    pass

        # Inject active analytical findings into system prompt
        if hasattr(self, "_analytical_spine"):
            _spine = self._analytical_spine
            _findings = getattr(_spine, "active_findings", [])
            if _findings:
                _high_conf = [f for f in _findings if getattr(f, "calibrated_confidence", 0) >= 0.75]
                if _high_conf:
                    _flines = ["[Analytics Engine Analytical Context]"]
                    for _f in _high_conf[:5]:
                        _flines.append(
                            f"  [{_f.category}] {_f.title} "
                            f"(confidence={_f.calibrated_confidence:.2f}): "
                            f"{getattr(_f, 'description', '')[:120]}")
                    parts.append("\n".join(_flines))
            # Inject trust score and active domain
            _ts = getattr(_spine, "trust_score", None)
            _lens = getattr(_spine, "active_lens", None)
            _arch = getattr(_spine, "active_archetype", None)
            if _ts is not None or _lens or _arch:
                _meta = []
                if _ts is not None:
                    _meta.append(f"trust_score={_ts:.2f}")
                if _arch:
                    _meta.append(f"archetype={_arch}")
                if _lens:
                    _meta.append(f"domain_lens={getattr(_lens, 'name', str(_lens))}")
                if _meta:
                    parts.append(f"[Analytics Engine Meta] {' · '.join(_meta)}")

        raw_sys = "\n\n".join(parts)

        # v20: Apply ContextBudgetManager to keep total within context window
        if hasattr(self, "_ctx_budget"):
            allocated = self._ctx_budget.allocate(
                system_prompt=raw_sys,
                history=list(self.history),
            )
            return allocated["system_prompt"]
        return raw_sys

    # ── TOOL_REGISTRY: wire per-instance context-sensitive handlers ──────────
    def _register_tool_handlers(self) -> None:
        """
        Register tool handlers that require Agent instance context (ws, hw, mem,
        scheduler, provider).  Called once at __init__.  Calling this again after
        workspace reconfiguration refreshes the closures — safe and idempotent.

        Stateless tools (web_search, python_exec) can be registered globally;
        we re-register them here too for consistency so the registry is always
        the single source of truth.
        """
        ws  = self.cfg.workspace
        aox = self.cfg.allow_outside
        hw  = self._hw
        mem = self.memory
        prov= self.cfg.provider
        mdl = self.cfg.model

        _handlers: dict[str, Callable] = {
            "shell":         lambda a: _tool_shell(
                                a.get("command",""), a.get("timeout",15), ws, aox),
            "read_file":     lambda a: _tool_read(
                                a.get("path",""), a.get("encoding","utf-8")),
            "write_file":    lambda a: _tool_write(
                                a.get("path",""), a.get("content",""), ws),
            "python_exec":   lambda a: _tool_python(
                                a.get("code",""), a.get("timeout",10)),
            "web_search":    lambda a: _tool_search(
                                a.get("query",""), a.get("max_results",5)),
            "heartbeat_add": lambda a: (
                self._scheduler.add(
                    a.get("name","job"), a.get("message",""), a.get("schedule","1h"))
                or f"[heartbeat scheduled: {a.get('name')} @ {a.get('schedule')}]"
            ) if self._scheduler else
                "[heartbeat_add: no scheduler attached to this agent]",
            "analyze_image": lambda a: _tool_analyze_image(
                                a.get("path",""), a.get("question","Describe this image."), hw),
            "build_skill":   lambda a: _tool_build_skill(
                                a.get("description",""), ws, prov, mdl),
            "ingest":        lambda a: _tool_ingest(
                                a.get("path_or_url",""), ws, mem),
            "run_analysis":  lambda a: _tool_run_analysis(
                                a.get("dataset_path",""), a.get("task","eda"),
                                a.get("target_col",""), a.get("config",{}), ws),
            "train_model":   lambda a: _tool_train_model(
                                a.get("dataset_path",""), a.get("target_col",""),
                                a.get("model_type","auto"), a.get("config",{}), ws,
                                getattr(self,"_tracker",None)),
            "finetune":      lambda a: _tool_finetune(
                                a.get("base_model",""), a.get("dataset_path",""),
                                a.get("output_dir",""), a.get("config",{}), ws),
            "vision_task":   lambda a: _tool_vision_task(
                                a.get("path",""), a.get("task","classify"),
                                a.get("model","auto"), hw),
            "speech":        lambda a: _tool_speech(
                                a.get("audio_path",""), a.get("task","transcribe"),
                                a.get("language","en"), hw),
            # ── v16: Browser automation (Playwright) ─────────────────────
            "browser_open":  lambda a: get_browser_session(
                                self.cfg.session_id if hasattr(self.cfg, "session_id")
                                else "default").open(
                                    a.get("url",""), a.get("timeout_ms", 15000)),
            "browser_screenshot": lambda a: get_browser_session(
                                self.cfg.session_id if hasattr(self.cfg, "session_id")
                                else "default").screenshot(
                                    Path(ws) if isinstance(ws, (str, Path)) else ws,
                                    a.get("selector","")),
            "browser_click": lambda a: get_browser_session(
                                self.cfg.session_id if hasattr(self.cfg, "session_id")
                                else "default").click(
                                    a.get("selector",""), a.get("timeout_ms", 5000)),
            "browser_fill":  lambda a: get_browser_session(
                                self.cfg.session_id if hasattr(self.cfg, "session_id")
                                else "default").fill(
                                    a.get("selector",""), a.get("value","")),
            "browser_extract": lambda a: get_browser_session(
                                self.cfg.session_id if hasattr(self.cfg, "session_id")
                                else "default").extract(a.get("selector","body")),
            # ── v16: Computer use (desktop automation) ────────────────────
            "computer_screenshot": lambda a: _tool_computer_screenshot(
                                Path(ws) if isinstance(ws, (str, Path)) else ws),
            "computer_click": lambda a: _tool_computer_click(
                                int(a.get("x", 0)), int(a.get("y", 0)),
                                a.get("button", "left")),
            "computer_type":  lambda a: _tool_computer_type(
                                a.get("text",""), float(a.get("interval", 0.02))),
            # ── v16: Voice pipeline (STT) ─────────────────────────────────
            "voice_transcribe": lambda a: get_voice_pipeline().transcribe(
                                a.get("audio_path","")),
            "voice_speak":    lambda a: str(
                                get_voice_pipeline().speak(a.get("text",""))),
            # ── Skill tools: lazy load + self-authoring ────────────────────
            "read_skill":     lambda a: read_skill_content(ws, a.get("skill_name","")),
            "skill_write":    lambda a: _tool_skill_write(
                                a.get("skill_name",""), a.get("description",""),
                                a.get("skill_md",""), a.get("tool_py",""),
                                a.get("requirements",""), ws, self),
        }
        for name, handler in _handlers.items():
            # Upsert: keep existing schema, replace handler only
            existing_schema = next(
                (s for s in TOOL_REGISTRY._schemas
                 if s["function"]["name"] == name), None)
            if existing_schema is None:
                existing_schema = next(
                    (s for s in BUILTIN_TOOLS
                     if s["function"]["name"] == name), None)
            if existing_schema:
                TOOL_REGISTRY.register(existing_schema, handler)
            else:
                TOOL_REGISTRY._handlers[name] = handler

    # ── Low-level LLM call ──────────────────────────────────────────────────
    def _llm(self, messages: list[dict], extra_sys: str = "",
             thinking: bool = False, user_query: str = "") -> str:
        sys_msg = {"role": "system", "content": self._sys(extra_sys, user_query=user_query)}
        out = ""
        # Use TOOL_REGISTRY.get_tools() so dynamically registered tools are included
        active_tools = TOOL_REGISTRY.get_tools() if self.cfg.use_tools else None
        for tok in self.cfg.provider.complete(
            [sys_msg] + messages,
            model=self.cfg.model, stream=False,
            thinking=thinking, budget=self.cfg.budget,
            tools=active_tools,
        ):
            out += tok
        return out.strip()

    # ── Memory distillation ─────────────────────────────────────────────────
    async def _allm(self, messages: list[dict], extra_sys: str = "",
                    thinking: bool = False, user_query: str = "") -> str:
        """Async version of _llm()."""
        sys_msg = {"role": "system", "content": self._sys(extra_sys, user_query=user_query)}
        out = ""
        active_tools = TOOL_REGISTRY.get_tools() if self.cfg.use_tools else None
        async for tok in self.cfg.provider.acomplete(
            [sys_msg] + messages,
            model=self.cfg.model, stream=False,
            thinking=thinking, budget=self.cfg.budget,
            tools=active_tools,
        ):
            out += tok
        return out.strip()

    async def _maybe_distil(self) -> None:
        if len(self.history) < self.cfg.memory_window: return
        _hist = [m for m in self.history[:-2]
                 if m.get("role") in ("user", "assistant")]
        # Use the CONSOLIDATOR specialist (smallest model, no thinking)
        consolidator = self._specialist_pool.get(AgentRole.CONSOLIDATOR)
        if consolidator:
            combined = "\n".join(
                f"{m['role']}: {m['content'][:200]}" for m in _hist[-8:])
            summary = await consolidator.arun(combined)
        else:
            summary = await self._allm(_hist, extra_sys=_MEM_SYS)
        self._distilled = summary

        # ── Context compaction ────────────────────────────────────────────
        # Archive full history to episodic BEFORE discarding — nothing is lost,
        # it's queryable via memory.recall() and recent_episodes().
        self.memory.record_episode(
            "\n".join(
                f"{m['role']}: {m['content'][:300]}" for m in _hist[-20:]),
            {"source": "context_compaction", "session": self._session_id,
             "turns_archived": len(_hist)})
        # Consolidate all three memory layers with the summary
        self.memory.consolidate(summary)
        # Compact: replace history with single summary message + last 2 turns.
        # Token reduction is typically 80-90% of the pre-compaction window.
        self.history = [{"role": "system",
                         "content": f"[Compacted context — {len(_hist)} turns archived]\n{summary}"}
                        ] + self.history[-2:]

    # ── Tool dispatch ───────────────────────────────────────────────────────
    # Destructive tool names checked by autonomy_level=1
    _DESTRUCTIVE_TOOLS = {"shell", "write_file", "python_exec",
                           "train_model", "finetune"}

    async def _adispatch(self, name: str, args: dict,
                        log: "Callable[[str], None] | None" = None) -> str:
        """Async version of _dispatch()."""
        ws  = self.cfg.workspace

        level = self.cfg.autonomy_level
        if level == 0 or (level == 1 and name in self._DESTRUCTIVE_TOOLS):
            if sys.stdin.isatty():
                prompt_str = (f"[autonomy={level}] Allow tool call: "
                              f"{name}({json.dumps(args)[:120]})? [y/N] ")
                # Async input is tricky in TTY; for now use thread-pool for the blocking input
                loop = asyncio.get_running_loop()
                try:
                    ans = await loop.run_in_executor(None, input, prompt_str)
                    ans = ans.strip().lower()
                except EOFError:
                    ans = "n"
                if ans not in ("y", "yes"):
                    return f"[BLOCKED by autonomy_level={level}: user denied {name}]"
            else:
                # Non-TTY / server context — cannot prompt; auto-approve but
                # write an explicit audit entry so the bypass is always visible.
                _bypass_msg = (
                    f"[AUTONOMY_BYPASS: autonomy_level={level}, non-TTY context, "
                    f"auto-approved {name}({json.dumps(args)[:80]})]")
                _al = self.cfg.workspace / "logs"
                _al.mkdir(exist_ok=True)
                with open(_al / "autonomy_bypasses.jsonl", "a", encoding="utf-8") as _f:
                    _f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                         "tool": name, "level": level,
                                         "args_preview": _mask_secrets(json.dumps(args))[:120]
                                         }) + "\n")
                if log:
                    log(f"  {yellow(chr(9888) + ' AUTONOMY_BYPASS')} {_bypass_msg[:120]}")
                log.info('autonomy_bypass', extra={
                    'tool': name, 'level': level,
                    'args_preview': _mask_secrets(json.dumps(args))[:80]
                })

        hw = getattr(self, "_hw", None)
        # ── Route through TOOL_REGISTRY (includes dynamic tools) ─────────────
        handler = TOOL_REGISTRY.get_handler(name)
        if handler is not None:
            with span_tool(name, session=getattr(self, "_session_id", "") or ""):
                raw_result = await TOOL_REGISTRY.acall(name, args)
        else:
            # Auto-scaffold missing skill, register it, then retry once.
            raw_result = f"[unknown tool: {name}]"
            if name and name.isidentifier():
                try:
                    _mkt = SkillMarketplace(ws, hub_url="")
                    _new_path = _mkt.scaffold_new_skill(name)
                    self.skills = load_skills(ws)
                    self._register_tool_handlers()
                    _retry_handler = TOOL_REGISTRY.get_handler(name)
                    if _retry_handler is not None:
                        raw_result = await TOOL_REGISTRY.acall(name, args)
                        log.info("auto_skill_created",
                                 extra={"tool": name, "path": str(_new_path)})
                    else:
                        raw_result = (
                            f"[auto_skill_scaffolded: {name}] "
                            f"Skill template created at {_new_path}. "
                            f"Implement the handler and retry."
                        )
                except Exception as _ske:
                    log.debug("auto_skill_failed",
                              extra={"tool": name, "error": str(_ske)[:120]})
        # v21: Audit log + Prometheus metrics
        _al = get_audit_log()
        if _al:
            _al.append("tool_call", {
                "tool": name, "session": self._session_id,
                "args_preview": json.dumps(args, default=str)[:120],
                "result_preview": str(raw_result)[:120],
            })
        record_metric_tool(name, not str(raw_result).startswith("["))

        # Emit to local WebhookEventBus (WebSocket) + NATS mesh (cross-node)
        _event_payload = {
            "tool":    name,
            "session": self._session_id,
            "preview": str(raw_result)[:300],
            "ts":      time.time(),
        }
        _bus = get_event_bus()
        if _bus is not None:
            try:
                _bus.emit("tool_result", _event_payload)
            except Exception:
                pass
        _nbus = get_nats_bus()
        if _nbus is not None:
            _nbus.emit("tool_result", _event_payload,
                        session_id=self._session_id)

        log.info('tool_dispatch', extra=ctx_log_extra({
            'tool': name,
            'result_preview': str(raw_result)[:120],
        }))
        raw_result = str(raw_result) if not isinstance(raw_result, str) else raw_result
        # Reload skill registry so newly built skills are immediately usable
        if name == "build_skill" and "[build_skill error" not in str(raw_result):
            self.skills = load_skills(ws)
            # Re-register handlers now that skills list changed
            self._register_tool_handlers()

        # ── SemanticGuard on every tool result ───────────────────────────────
        guarded = semantic_guard(raw_result)
        if guarded is not None:
            if log:
                log(f"  {yellow('⚠ SemanticGuard')} {guarded[:100]}")
            return guarded
        return raw_result

    # ── Public: streaming chat turn ─────────────────────────────────────────
    async def achat(self, user: str, emit: Callable[[str], None] = lambda _: None) -> str:
        """achat Queue must have maxsize=256"""
        return await self.chat(user, emit)

    async def chat(self, user: str,
             emit: Callable[[str], None] = lambda _: None) -> str:
        await self._maybe_distil()
        # Cap content length to avoid blowing the context window
        _MAX_MSG = 32_000   # characters (~8K tokens at 4 chars/tok)
        user_capped = user[:_MAX_MSG] + (" …[truncated]" if len(user) > _MAX_MSG else "")
        self.history.append({"role": "user", "content": user_capped})
        self.memory.append_session(self._session_id, "user", user_capped)
        sys_msg = {"role": "system", "content": self._sys(user_query=user)}
        response = ""
        # Use TOOL_REGISTRY so dynamic tools appear in LLM tool-call payloads
        active_tools = TOOL_REGISTRY.get_tools() if self.cfg.use_tools else None
        _task_id = f"chat_{self._session_id}"
        # v29: ContextualBanditRouter previously only ever recorded rewards — select()
        # was never called, so learned routing never influenced model choice. Wire it
        # in here with a safe fallback to the configured model on any failure/cold-start.
        _chat_model = self.cfg.model
        if hasattr(self, "_bandit"):
            try:
                _picked = self._bandit.select({
                    "complexity": "medium",  # chat() has no IntentLayer classification step
                    "dataset_archetype": self._analytical_spine.active_archetype,
                    "trust_score": self._analytical_spine.trust_score,
                    "domain_lens": getattr(self._analytical_spine.active_lens, "name", "none")
                                   if self._analytical_spine.active_lens else "none",
                })
                if _picked:
                    _chat_model = _picked
            except Exception as _bsel_err:
                log.debug("bandit_select_error", extra={"error": str(_bsel_err)[:120]})
        self._cost.start_task(_task_id, model=_chat_model)
        # Apply ContextBudgetManager: trim history to keep within context window
        if hasattr(self, "_ctx_budget"):
            _alloc = self._ctx_budget.allocate(
                system_prompt=sys_msg.get("content", ""),
                history=list(self.history))
            _hist_to_send = _alloc["history"]
        else:
            _hist_to_send = self.history
        try:
          async for tok in self.cfg.provider.acomplete(
              [sys_msg] + _hist_to_send,
              model=_chat_model, stream=True,
              thinking=self.cfg.thinking, budget=self.cfg.budget,
              tools=active_tools,
          ):
              response += tok
              try:
                  emit(tok)
              except Exception as _exc:
                  log.debug('emit_callback_error',
                            extra={'error': str(_exc)})
        finally:
          # v24: Accurate token count via count_tokens()
          _prompt_text = " ".join(m.get("content","") for m in [sys_msg]+_hist_to_send)
          try:
              self._cost.record(
                  prompt_tokens=count_tokens(_prompt_text),
                  completion_tokens=count_tokens(response),
                  task_id=_task_id)
          except BudgetExceededError as _be:
              log.warning("chat_budget_exceeded",
                          extra={"session": self._session_id, "spent": _be.spent})
          self._cost.finish_task(_task_id)
        response_capped = response[:_MAX_MSG] + (" …[truncated]" if len(response) > _MAX_MSG else "")
        self.history.append({"role": "assistant", "content": response_capped})
        self.memory.append_session(self._session_id, "assistant", response_capped)
        if hasattr(self, "_bandit"):
            try:
                _reward = 1.0 if response.strip() else 0.1
                self._bandit.record(_chat_model, {
                    "complexity": "medium",
                    "dataset_archetype": self._analytical_spine.active_archetype,
                    "trust_score": self._analytical_spine.trust_score,
                    "domain_lens": getattr(self._analytical_spine.active_lens, "name", "none")
                                   if self._analytical_spine.active_lens else "none",
                }, reward=_reward)
            except Exception as _brec_err:
                log.debug("bandit_record_error", extra={"error": str(_brec_err)[:120]})

        # Learning Engine observe chat turn for self-learning
        try:
            if hasattr(self._genesis, "observe"):
                self._genesis.observe(
                    state={
                        "query":   user_capped[:200],
                        "session": self._session_id,
                        "model":   _chat_model,
                    },
                    action={"type": "chat"},
                    outcome={
                        "reward":         1.0 if response.strip() else 0.1,
                        "response_chars": len(response),
                    },
                )
        except Exception as _gen_err:
            log.debug("genesis_observe_error", extra={"error": str(_gen_err)[:80]})

        # MessageProtocol wrapping for multi-agent transparency logging
        if self._bridge:
            try:
                _wrapped = self._msg_protocol.wrap(
                    sender   = self._session_id,
                    receiver = "orchestrator",
                    msg_type = "chat_response",
                    content  = {"preview": response_capped[:200]},
                )
                if self._msg_protocol.validate(_wrapped):
                    self._observer.record_protocol_wrap(
                        sender   = _wrapped["from"],
                        receiver = _wrapped["to"],
                        msg_type = _wrapped["type"],
                    )
            except Exception as _prot_err:
                log.debug("protocol_wrap_error", extra={"error": str(_prot_err)[:80]})

        return response

    # ── Public: multi-agent task (TaskPipeline loop) ─────────────────────────────
    async def run_task(self, task: str,
                 log: Callable[[str], None] = print) -> str:
        """
        Production TaskPipeline (Async-Native v29):
          Step 1 — PLAN    (PLANNER specialist, thinking ON, large model)
          Step 2 — EXECUTE (EXECUTOR specialist, complexity-routed model)
          Step 3 — CRITIQUE(CRITIC specialist, medium model)
          Step 4 — VERIFY  (VerifierLayer, cheapest model)
        WorkflowEngine checkpoints each step; crash-safe replay from last commit.
        AgentObserver records all spans for behavioral drift detection.
        DecisionQueue gates destructive calls when autonomy < 2.
        """
        # ── Master delegation — try slave first if MASTER role ────────────
        if self._bridge and self._bridge.role == SystemRole.ORCHESTRATOR:
            delegated = await self._bridge.adelegate_task(task)
            if delegated:
                return delegated


        # ── Step 0: INTENT — structured task spec + blackboard reset ─
        _task_spec = build_task_spec(
            task, session_id=self._session_id, user_id="",
            context={"model": self.cfg.model})
        self._blackboard.clear()
        self._scratchpad.clear()
        self._blackboard.write("task_spec", _dc.asdict(_task_spec),
                               entry_type="fact", author="intent_layer")
        _intent_msg = (f'Intent: priority={_task_spec.priority}  '
                       f'complexity={_task_spec.complexity.name}  '
                       f'constraints={len(_task_spec.constraints)}')
        log(f"  {dim(_intent_msg)}")

        # ── Step 0.5: Analytics Engine RECONNAISSANCE ───────────────────────────
        if _task_spec.analytical_mode:
            log(f"  {cyan('▸ Analytics Engine Reconnaissance')} …")
            # Simplified: assuming first file mentioned is the dataset
            files = _task_spec.context.get("files", [])
            if files:
                try:
                    recon_findings = self._wave_controller.analyze(files[0], max_wave=1)
                    self._analytical_spine.active_findings.extend(recon_findings)
                    self._blackboard.write("prism_recon", [f.model_dump() for f in recon_findings],
                                           entry_type="fact", author="prism_recon")
                except Exception as e:
                    log(f"  {red('⚠ Recon failed')}: {str(e)}")

        # ── Step 1: PLAN ─────────────────────────────────────────────────
        log(f"\n{cyan('▸ Planner')} …")
        planner = self._specialist_pool.get(AgentRole.PLANNER)
        t_plan  = time.perf_counter()
        # Inject relevant SOPs into the planner system prompt
        _sop_context = self._sop.relevant(task, max_docs=2)
        # v28.1: PromptEvolution selects the best planner prompt variant
        _evolved_plan_sys = self._prompt_evolver.select("planner") or _PLAN_SYS
        _prism_ctx   = _task_spec.context.get("_prism_spine", "")
        _plan_sys    = (_evolved_plan_sys + (_sop_context if _sop_context else "")
                        + (f"\n\n{_prism_ctx}" if _prism_ctx else ""))
        # v28.1: TreeOfThought multi-path planning (falls back to single-plan)
        _bb_context = self._blackboard.to_context(max_entries=10)
        _tot_plan, _tot_branch = await self._tot.areason(
            task, context=_bb_context, plan_sys=_plan_sys,
            genesis=self._genesis, arch_id=self._analytical_spine.active_archetype)
        if _tot_plan:
            raw_plan = json.dumps(_tot_plan)
            self._scratchpad.note(
                f"ToT selected branch {_tot_branch.branch_id} "
                f"(score={_tot_branch.score:.2f}): {_tot_branch.rationale[:100]}",
                kind="reasoning", step_id=0)
            self._blackboard.write("plan_branch", {
                "branch_id": _tot_branch.branch_id,
                "score": _tot_branch.score,
                "rationale": _tot_branch.rationale[:200]},
                entry_type="decision", author="planner")
        elif planner:
            raw_plan = await planner.arun(task)
        else:
            raw_plan = await self._allm(
                [{"role": "user", "content": task}],
                extra_sys=_plan_sys, thinking=True, user_query=task)
        self._observer.record_llm_call(
            model=self.cfg.model,
            prompt_chars=len(task),
            response_chars=len(raw_plan),
            latency_ms=(time.perf_counter() - t_plan) * 1000,
            thinking=True, step_id=0)
        self._observer.record_reasoning(step_id=0, text=raw_plan, phase="plan")

        try:
            clean = re.sub(r"```[a-zA-Z]*", "", raw_plan).strip()
            steps = json.loads(clean)
            if not isinstance(steps, list):
                raise ValueError
            # v22: validate plan schema
            for _s in steps[:1]:   # validate first step as sample
                _ok, _err = SCHEMA_REGISTRY.validate("plan_output", _s)
                if not _ok:
                    log.debug("plan_schema_mismatch", extra={"error": _err[:80]})
        except Exception:
            steps = [{"step": 1, "action": task, "tool": "none", "args": {}}]

        # ── v28.1: Cognitive Reflection Loop (Self-Critique → Replan) ──────
        # Formal Self-Critique → Verification → Revision → Replan Trigger cycle.
        _reflection_attempts = 0
        while _reflection_attempts < 2:
            should_replan, revision = await self._reflector.areflect(task, steps, context=_bb_context)
            if not should_replan:
                break

            _reflection_attempts += 1
            log(f"  {yellow('▸ Cognitive Reflection')} (attempt {_reflection_attempts}): Re-planning... {revision[:80]}...")
            self._blackboard.write(f"reflection_critique_{_reflection_attempts}", revision,
                                   entry_type="critique", author="reflector")

            # Trigger a new planning pass with critique context
            replan_context = f"{_bb_context}\n\nCRITIQUE OF PREVIOUS PLAN:\n{revision}"
            _tot_plan, _tot_branch = await self._tot.areason(task, context=replan_context, plan_sys=_plan_sys)
            if _tot_plan:
                steps = _tot_plan
            else:
                raw_plan = await planner.arun(f"Previous plan failed critique. Task: {task}\nCritique: {revision}")
                try:
                    steps = json.loads(re.sub(r"```[a-zA-Z]*", "", raw_plan).strip())
                except (json.JSONDecodeError, ValueError):
                    break  # fall back to original steps if parsing fails

        # ── Create workflow state (enables crash recovery) ─────────────────
        # ── v30: Value Alignment Gate ─────────────────────────────────────
        log(f"  {cyan('▸ Alignment Oracle')} …")
        is_safe, rationale = await self._alignment_oracle.check(task, steps, context=_bb_context)
        if not is_safe:
            log(f"  {red('⚠ ALIGNMENT VIOLATION')} - {rationale[:120]}")
            return f"Task blocked by ValueAlignmentOracle: {rationale}"

        wf_state = self._workflow_engine.create(task, steps)
        log(f"  {dim('Workflow: ' + wf_state.task_id + '  (' + str(len(steps)) + ' steps)')}")

        # TaskHandoff — claim the task lock so peer agents skip duplicates
        _handoff_claimed = self._task_handoff.claim(wf_state.task_id, self._session_id)
        if not _handoff_claimed:
            log(f"  {yellow('⚠ TaskHandoff')} task already claimed by peer; proceeding anyway")
        self._observer.record_task_handoff(
            task_id=wf_state.task_id,
            from_agent="dispatcher",
            to_agent=self._session_id,
            success=_handoff_claimed,
        )

        results: list[str] = []
        all_tool_results: list[str] = []
        _TOOL_NAMES = set(TOOL_REGISTRY.names())

        async def _arepair_json(raw: str, schema: str) -> str:
            return await self._allm(
                [{"role": "user", "content":
                  f"Fix this invalid JSON to match schema:\n{schema}\n\nJSON:\n{raw}\n"
                  "Output ONLY corrected JSON."}],
                extra_sys="You are a JSON repair assistant.")

        async def _ado_execute(ws: WorkflowStep) -> str:
            # Step 2: EXECUTE per step (Async-Native v29)
            t_exec = time.perf_counter()
            log(f"\n{green(f'▸ Step {ws.step_id}')}: {ws.action[:120]}")
            complexity = _classify_complexity(ws.action, ws.tool)

            if ws.tool and ws.tool != "none" and ws.tool in _TOOL_NAMES:
                # CapabilityPolicy gate — pre-authorize before dispatch
                if self.cfg.autonomy_level < 2 and ws.tool in CapabilityPolicy.DESTRUCTIVE:
                    decision = self._decision_queue.enqueue(
                        ws.tool, ws.args, reason=f"Step {ws.step_id}: {ws.action[:80]}",
                        session_id=self._session_id)
                    if sys.stdin.isatty():
                        approved = await self._decision_queue.await_for(decision.decision_id, timeout=30.0)
                        if approved is False: return f"[BLOCKED: rejected {ws.tool}]"
                        if approved is None and self.cfg.autonomy_level == 0:
                            return f"[BLOCKED: timeout {ws.tool}]"
                res = await self._adispatch(ws.tool, ws.args, log=log)
            else:
                executor = self._specialist_pool.get(AgentRole.EXECUTOR)
                raw = await executor.arun(ws.action) if executor else await self._allm(
                    [{"role": "user", "content": ws.action}], extra_sys=_EXEC_SYS)
                try:
                    res = json.loads(re.sub(r"```[a-zA-Z]*", "", raw).strip()).get("result", raw)
                except Exception:
                    res = raw

            exec_ms = (time.perf_counter() - t_exec) * 1000
            self._observer.record_tool_call(ws.tool or "llm", latency_ms=exec_ms, success=True, result_preview=str(res)[:80])

            # ── Step 3: CRITIQUE (per-step async v29) ───────────────
            if self.cfg.critic:
                # v29: Statistically grounded critique
                prism_failures = self._aegis.analytical_critic_gate(ws.action, str(res), self._analytical_spine)
                for pf in prism_failures:
                    self._observer.record_critic(ws.step_id, False, pf["type"])
                    self._observer.record_aegis_event(
                        event_type=pf["type"], component="analytical_critic_gate",
                        severity="warn", detail=pf.get("reason", "")[:120])
                    log(f"  {red('⚠ Analytical Failure')}: {pf['type']} - {pf['reason'][:80]}")

                critic = self._specialist_pool.get(AgentRole.CRITIC)
                crit_prompt = f"Action: {ws.action}\nResult: {str(res)[:600]}"
                crit_raw = await critic.arun(crit_prompt) if critic else await self._allm(
                    [{"role": "user", "content": crit_prompt}], extra_sys=_CRITIC_GATE_SYS)
                crit = await CriticResult.afrom_json(crit_raw, repair_fn=_arepair_json)
                self._observer.record_critic(ws.step_id, crit.passed, crit.category)

                #  ConflictResolution: arbitrate when Resilience Layer and LLM critic disagree
                if prism_failures and crit.passed:
                    # Resilience Layer says fail, LLM critic says pass — use ConflictResolution
                    # Resilience Layer gets priority_a=2 (data-driven), LLM critic priority_b=1
                    ae_verdict = prism_failures[0]["type"]
                    chosen = self._conflict_resolver.arbitrate(
                        proposal_a=ae_verdict,
                        proposal_b="llm_pass",
                        priority_a=2,
                        priority_b=1,
                    )
                    self._observer.record_conflict_resolution(
                        method="arbitrate",
                        winner=chosen,
                        options_count=2,
                    )
                    if chosen == ae_verdict:
                        crit.passed = False
                        log(f"  {yellow('▸ ConflictResolution')} Resilience Layer overrides LLM critic")

                if not crit.passed and crit.fix_hint:
                    log(f"  {yellow('▸ Critique Fix')}: {crit.fix_hint[:60]}")
                    res = await self._allm([{"role":"user","content":f"Fix: {crit.fix_hint}\nTask: {ws.action}"}])

            log(f"  {dim('→')} {str(res)[:200]}")
            self._blackboard.write(f"step_{ws.step_id}_result", str(res)[:500], author=ws.tool or "executor")
            if ws.tool and ws.tool != "none":
                all_tool_results.append(f"[Step {ws.step_id}/{ws.tool}]: {str(res)[:200]}")
            return str(res)

        # ── EXECUTE (Async DAG v29) ──────────────────────────────────
        log(f"  {green('▸ Executing DAG workflow')} (parallelism={self.cfg.max_steps})")
        await self._dag_executor.arun(wf_state, _ado_execute)
        results = [s.result for s in wf_state.steps if s.result]

        # ── Step 4: VERIFY ─────────────────────────────────────────
        final_response = "\n\n".join(str(r) for r in results)
        if all_tool_results:
            # v29: Pass analytical spine for finding-to-claim validation
            verification = await self._verifier.aversion(
                final_response, "\n".join(all_tool_results),
                spine=self._analytical_spine)
            for vr in verification:
                self._observer.record_hallucination(vr.claim, vr.verdict)
            final_response = self._verifier.annotate(final_response, verification)

        # ── Export traces + write audit log ──────────────────────────────
        self._observer.export_jsonl()
        self._observer.export_otel()
        log_path = self.cfg.workspace / "logs" / f"task_{self._session_id}.json"
        log_path.parent.mkdir(exist_ok=True)
        summary = self._observer.summary()
        summary["workflow_id"] = wf_state.task_id
        log_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        _obs_line = (
            f"Observer: {summary['total_tokens_out']} tokens out  "
            f"critic_pass={summary['critic_pass_rate']:.0%}  "
            f"cost~${summary['estimated_cost']:.5f}")
        log(f"\n{dim(_obs_line)}")

        # ── v28.1: Reward Signal → Adaptation Pipeline ──────────────────────
        try:
            _budget_val = summary.get("total_tokens_out", 0)
            _reward = compute_reward(self._observer, budget=_budget_val, spine=self._analytical_spine)
            # Feed reward to ContextualBanditRouter (v29 archetype-enriched)
            if hasattr(self, "_bandit") and self.cfg.model:
                _ctx_r = {
                    "complexity": _task_spec.complexity.value if hasattr(_task_spec.complexity, "value") else "medium",
                    "dataset_archetype": self._analytical_spine.active_archetype,
                    "trust_score": self._analytical_spine.trust_score,
                    "domain_lens": getattr(self._analytical_spine.active_lens, "name", "none")
                                   if self._analytical_spine.active_lens else "none",
                }
                self._bandit.record(self.cfg.model, _ctx_r,
                                    reward=_reward.reward, latency_ms=0)
            # Feed reward to PromptEvolution (v29 archetype-aware)
            _used_plan_sys = _evolved_plan_sys if '_evolved_plan_sys' in dir() else _PLAN_SYS
            self._prompt_evolver.record("planner", _used_plan_sys, _reward.reward)
            # Workflow Compression — detect repeated patterns (v29 archetype-aware)
            _completed_steps = [s for s in wf_state.steps
                                if s.status == StepStatus.SUCCESS]
            _skill_template = self._wf_compressor.record(_completed_steps, arch_id=self._analytical_spine.active_archetype)
            if _skill_template:
                _skill_path = self.cfg.workspace / "skills" / "auto-compressed" / "SKILL.md"
                _skill_path.parent.mkdir(parents=True, exist_ok=True)
                _skill_path.write_text(_skill_template, encoding="utf-8")
                log(f"  {cyan('▸ Workflow compressed')} → auto-skill saved")
            _reward_msg = (f'Reward: {_reward.reward:.3f}  '
                           f'(critic={_reward.critic_pass_rate:.2f} '
                           f'tools={_reward.tool_success_rate:.2f} '
                           f'eff={_reward.token_efficiency:.2f})')
            log(f"  {dim(_reward_msg)}")
        except Exception as _rew_err:
            log.debug("reward_computation_error",
                      extra={"error": str(_rew_err)[:120]}) if hasattr(log, "debug") else None

        # Analytics Engine decomposed reward (analytical dimensions) — runs after
        # scalar compute_reward so both reward tracks update subsystems
        try:
            _dr = _run_analytical_reward(
                observer      = self._observer,
                spine         = self._analytical_spine,
                budget        = _budget_val,
                critic        = getattr(self, "_analytical_critic", None),
                delayed_queue = getattr(self, "_delayed_queue", None),
                prompt_evol   = getattr(self, "_analytical_evolver", None),
                workflow_cmp  = self._wf_compressor,
                bandit        = getattr(self, "_bandit", None),
                plan_prompt   = (_evolved_plan_sys
                                 if "_evolved_plan_sys" in dir() else _PLAN_SYS),
            )
            if _dr:
                # trace Learning Engine learn event in observer
                self._observer.record_genesis_learn(
                    trajectory_id = wf_state.task_id,
                    reward        = _dr.composite,
                    outcome       = f"aq={_dr.analytical_quality:.3f} n={_dr.novelty:.3f}",
                )
                log(f"  {cyan('▸ Analytics Engine Reward')} composite={_dr.composite:.3f} "
                    f"aq={_dr.analytical_quality:.3f} novelty={_dr.novelty:.3f}")
                # Persist average confidence for drift detection
                if self._analytical_spine.active_findings:
                    _avg_conf = (
                        sum(f.calibrated_confidence
                            for f in self._analytical_spine.active_findings)
                        / len(self._analytical_spine.active_findings)
                    )
                    self.memory.set("_prism_last_avg_confidence",
                                    str(round(_avg_conf, 4)))
        except Exception as _dr_err:
            log.debug("prism_decomposed_reward_error",
                      extra={"error": str(_dr_err)[:120]})

        # Release TaskHandoff lock at task completion
        if _handoff_claimed:
            self._task_handoff.release(wf_state.task_id, self._session_id)

        # ── Step 6: ANALYTICAL MEMORY PERSISTENCE ───────────────────
        # Use assert_prism_finding() for richer SSS integration (finding_id index)
        for finding in self._analytical_spine.active_findings:
            if finding.calibrated_confidence >= 0.7:
                try:
                    self._semantic_state.assert_prism_finding(finding)
                    self._observer.record_prism_finding(
                        finding_id = getattr(finding, "finding_id", ""),
                        category   = finding.category,
                        confidence = finding.calibrated_confidence,
                        layer      = getattr(finding, "source_layer", "unknown"),
                        novelty    = getattr(finding, "novelty", 0.0),
                    )
                except Exception:
                    # Fallback to manual assert if assert_prism_finding() absent
                    self._semantic_state.assert_fact(
                        finding.category, "finding", finding.title, finding.description,
                        confidence=finding.calibrated_confidence,
                        source=f"prism_{finding.source_layer}")

        # Persist domain lens for drift detection
        _active_lens = (getattr(self._analytical_spine.active_lens, "name", "")
                        if self._analytical_spine.active_lens else "")
        if _active_lens:
            self.memory.set("_prism_current_domain", _active_lens)

        # Save Genesis state (Tier 2)
        self._genesis._save()

        # Step 7: PROACTIVE SCHEDULING
        # Check if any analytical alerts should fire
        prism_events = self._proactive._check_analytical_drift(time.time())
        for pe in prism_events:
            log(f"  {magenta('▸ Proactive Alert')}: {pe.title}")

        # v28.1: Clear blackboard at task end
        self._blackboard.clear()

        return final_response


    async def arun_task(self, task: str,
                        log_fn: Callable[[str], None] = print) -> str:
        """Async-native multi-agent task loop."""
        return await self.run_task(task, log=log_fn)

# ══════════════════════════════════════════════════════════════════════════════

# ──  Analytics Engine analytical component wiring (appended post-extraction) ───────
#
# Imports are guard-wrapped so the Agent remains fully functional when
# the prism sub-package is absent (e.g. minimal installs).
#
# — AnalyticalCriticGate
try:
    from essence.analytics.analysis_critic import (
        AnalyticalCriticGate, AnalyticalCriticResult,   # noqa: F401
        GenesisFeedbackRecord,                           # noqa: F401
    )
    _ANALYTICAL_CRITIC_AVAILABLE = True
except ImportError:
    _ANALYTICAL_CRITIC_AVAILABLE = False

# — AnalyticalVerifier
try:
    from essence.analytics.analysis_verifier import (
        AnalyticalVerifier, AnalyticalVerificationResult,  # noqa: F401
    )
    _ANALYTICAL_VERIFIER_AVAILABLE = True
except ImportError:
    _ANALYTICAL_VERIFIER_AVAILABLE = False

# — Decomposed reward
try:
    from essence.analytics.analytical_reward import (
        compute_analytical_reward, DecomposedReward,        # noqa: F401
        DelayedRewardQueue, route_decomposed_reward,        # noqa: F401
    )
    _ANALYTICAL_REWARD_AVAILABLE = True
except ImportError:
    _ANALYTICAL_REWARD_AVAILABLE = False

# — Analytical Prompt Evolver
try:
    from essence.analytics.analytical_prompt_evolver import (
        AnalyticalPromptEvolver, ANALYTICAL_ROLES,          # noqa: F401
    )
    _ANALYTICAL_EVOLVER_AVAILABLE = True
except ImportError:
    _ANALYTICAL_EVOLVER_AVAILABLE = False


def _build_analytical_components(
        spine: "AnalyticalStateBus",
        provider: Any = None,
        model: str = "",
        base_prompt_evol: Any = None,
) -> dict:
    """
    Construct the four  analytical components for an Agent instance.

    Returns a dict so Agent.__init__ can do:
        comps = _build_analytical_components(self._analytical_spine, ...)
        self._analytical_critic   = comps["critic"]
        self._analytical_verifier = comps["verifier"]
        self._delayed_queue       = comps["delayed_queue"]
        self._analytical_evolver  = comps["evolver"]

    All components are None-safe: if a component's package is absent the
    value is None and the call sites check before calling.
    """
    critic   = (AnalyticalCriticGate(spine=spine, provider=provider, model=model)
                if _ANALYTICAL_CRITIC_AVAILABLE else None)
    verifier = (AnalyticalVerifier(spine=spine, provider=provider, model=model)
                if _ANALYTICAL_VERIFIER_AVAILABLE else None)
    d_queue  = (DelayedRewardQueue()
                if _ANALYTICAL_REWARD_AVAILABLE else None)
    evolver  = (AnalyticalPromptEvolver(spine=spine, base_evol=base_prompt_evol)
                if _ANALYTICAL_EVOLVER_AVAILABLE else None)
    return {
        "critic":        critic,
        "verifier":      verifier,
        "delayed_queue": d_queue,
        "evolver":       evolver,
    }


def _run_analytical_critique(
        critic: "AnalyticalCriticGate | None",
        step_action: str,
        step_result: str,
        step_id: str = "",
) -> "AnalyticalCriticResult | None":
    """
     helper called inside the Step 3 CRITIQUE loop.
    Returns the AnalyticalCriticResult or None if the gate is unavailable.

    Usage in run_task():
        ac_result = _run_analytical_critique(
            self._analytical_critic, ws.action, str(res), ws.step_id)
        if ac_result and not ac_result.passed:
            log(f"  Analytical failure: {ac_result.category} — {ac_result.evidence}")
            # self-correction path: re-run step with fix_hint injected
    """
    if critic is None:
        return None
    try:
        return critic.validate(step_action, step_result, step_id)
    except Exception as e:
        log.debug("_run_analytical_critique error: %s", e)
        return None


def _run_analytical_verify(
        verifier: "AnalyticalVerifier | None",
        agent_output: str,
        tool_context: str = "",
) -> "AnalyticalVerificationResult | None":
    """
     helper called in Step 4 VERIFY.
    Returns AnalyticalVerificationResult or None.

    Usage in run_task():
        av_result = _run_analytical_verify(
            self._analytical_verifier, final_response,
            "\\n".join(all_tool_results))
        if av_result and av_result.needs_revision:
            final_response = av_result.annotated_output(final_response)
    """
    if verifier is None:
        return None
    try:
        return verifier.verify(agent_output, tool_context)
    except Exception as e:
        log.debug("_run_analytical_verify error: %s", e)
        return None


def _run_analytical_reward(
        observer:         Any,
        spine:            "AnalyticalStateBus | None",
        budget:           int = 0,
        user_feedback:    float = 0.5,
        critic:           "AnalyticalCriticGate | None" = None,
        delayed_queue:    "DelayedRewardQueue | None"   = None,
        prompt_evol:      Any = None,
        workflow_cmp:     Any = None,
        bandit:           Any = None,
        plan_prompt:      str = "",
        critic_prompt:    str = "",
) -> "DecomposedReward | None":
    """
     helper — compute + route decomposed reward.

    Drains Learning Engine feedback from the analytical critic before scoring,
    then routes each dimension to its target subsystem.

    Usage in run_task() Step after execution:
        dr = _run_analytical_reward(
            observer=self._observer, spine=self._analytical_spine,
            budget=_budget_val, critic=self._analytical_critic,
            delayed_queue=self._delayed_queue,
            prompt_evol=self._prompt_evolver, workflow_cmp=self._wf_compressor,
            bandit=getattr(self, "_bandit", None),
        )
        if dr:
            log(f"  Reward: composite={dr.composite:.3f} "
                f"analytical={dr.analytical_quality:.3f}")
    """
    if not _ANALYTICAL_REWARD_AVAILABLE or spine is None:
        return None
    try:
        genesis_feedback = critic.drain_feedback() if critic else []
        genesis_dicts    = [
            {"confirmed": r.confirmed, "category": r.category,
             "finding_id": r.finding_id}
            for r in genesis_feedback
        ]
        reward = compute_analytical_reward(
            observer=observer,
            spine=spine,
            budget=budget,
            user_feedback=user_feedback,
            genesis_feedback=genesis_dicts,
            delayed_queue=delayed_queue,
        )
        route_decomposed_reward(
            reward=reward,
            prompt_evol=prompt_evol,
            workflow_cmp=workflow_cmp,
            bandit=bandit,
            spine=spine,
            plan_prompt=plan_prompt,
            critic_prompt=critic_prompt,
        )
        return reward
    except Exception as e:
        log.debug("_run_analytical_reward error: %s", e)
        return None
