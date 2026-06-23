
"""
APDE Boot Kernel.
boot_kernel(config_path) -> Kernel
Kernel exposes exactly four public entry points: ingest_capsule, user_input, tick, audit.
"""
from __future__ import annotations
import dataclasses as _dc, logging, os, sys, time, uuid
from pathlib import Path
from typing import Any
from essence.apde_types import (
    CallClass, IntentCapsule, PlanDAG, Task, ExecResult, TaskState,
    SelfTestFailure, MigrationFailure, IncompleteToolRegistry,
    RetiredSubsystemError, APDE_NAMESPACE,
)
from essence.infra.runtime_manifest import RuntimeManifest, get_epoch_id
from essence.infra.capsule_store import (
    CapsuleRepository, PlanRepository, DeltaLedger, apply_migrations,
)
from essence.infra.capsule_store.canonicalization import hash_capsule
from essence.infra.context_view import ContextWindowManager
from essence.infra.sqe import StratumStore, SQESampler
from essence.infra.tbc import TBCClassifier
from essence.tools.governor import ResourceGovernor
from essence.tools.tool_belt import ToolBelt
from essence.security.guardrail_layer import GuardrailLayer
from essence.agents.planning import IntentCompressor, Decomposer
from essence.agents.planning.coverage import covers
from essence.agents.planning.disjointness import check_disjointness
from essence.agents.verification import RubricRegistry
from essence.agents.verification.rubric_assets import write_rubric_assets
from essence.agents.decision_guide import (
    RuleLibrary, RuleIndex, RuleSelector, RuleInjector,
)
from essence.channels.ratification import Ratifier
from essence.channels.pmp import PMPPipeline
from essence.channels.staging import StagingStore
from essence.agents.pipeline_executor import PipelineExecutor
from essence.agents.verifier import APDEVerifier
from essence.backends.apde_router import APDERouter

log = logging.getLogger("essence.boot")


@_dc.dataclass
class KernelSubsystems:
    """
    Typed container for all Kernel subsystems.

    All fields use Any to avoid circular imports at the dataclass definition
    site; concrete types are enforced by boot_kernel().
    """
    manifest:         Any
    governor:         Any
    router:           Any
    guardrails:       Any
    compressor:       Any
    decomposer:       Any
    executor:         Any
    apde_verifier:    Any
    rubric_registry:  Any
    rule_selector:    Any
    ctx_mgr:          Any
    capsule_repo:     Any
    plan_repo:        Any
    delta_ledger:     Any
    ratifier:         Any
    pmp:              Any
    sqe_sampler:      Any
    tbc:              Any
    all_tool_records: list
    autonomy_tier:    int
    epoch_id:         str
    intent_router:    Any = None
    task_router:      Any = None
    event_bus:        Any = None
    goal_manager:     Any = None
    audit_logger:     Any = None
    curiosity_engine: Any = None
    subagent_router:  Any = None
    staging:          Any = None
    heartbeat:        Any = None
    proactive:        Any = None
    capability_graph: Any = None
    twin:               Any = None
    intent_evolution:   Any = None
    opportunity_engine: Any = None
    kg:                 Any = None
    simulator:          Any = None
    state_detector:     Any = None
    trust_ledger:       Any = None
    model_fabric:       Any = None
    wisdom:             Any = None
    meta_reflector:     Any = None
    temporal:           Any = None
    research_engine:    Any = None
    memory_lifecycle:   Any = None
    attention:               Any = None
    objective_reconstructor: Any = None
    cognitive_health:        Any = None
    capability_retirement:   Any = None
    user_preference_engine:  Any = None
    meta:               Any = None
    # Wave 2 intelligence modules
    reminder_engine:         Any = None
    daily_briefing:          Any = None
    emotional_state:         Any = None
    knowledge_gap_detector:  Any = None
    session_replay:          Any = None
    learning_curve:          Any = None
    # Skill system
    skill_system:            Any = None


@_dc.dataclass
class KernelConfig:
    """Configuration for boot_kernel().  All fields have safe defaults."""
    workspace:         Path = _dc.field(default_factory=lambda: Path.home() / ".essence")
    manifest_path:     Path | None = None
    autonomy_tier:     int  = 2
    dev_mode:          bool = True
    plan_max_tokens:   int  = 8192
    exec_max_tokens:   int  = 16384
    verify_max_tokens: int  = 4096
    quota_limit:       int  = 10000


def _build_provider_fn(workspace: "Path") -> "Any":
    """
    Build a real provider function by instantiating ProviderChain from
    backends.adapters (the same chain the legacy Agent uses).
    Returns None if no backend is reachable so the caller can fall back to stub.
    """
    try:
        from essence.backends.adapters import ProviderChain
        from essence.backends.registry import BackendRegistry
        registry = BackendRegistry(workspace)
        providers = registry.build_providers()
        if not providers:
            return None
        chain = ProviderChain(providers)

        def _real_provider(messages: list[dict], model: str,
                           max_tokens: int, seed: int,
                           call_class: "Any" = None) -> str:
            try:
                chunks = list(chain.complete(
                    messages, model=model, max_tokens=max_tokens))
                return "".join(chunks)
            except Exception as _exc:
                log.warning("real_provider_failed", extra={"error": str(_exc)[:120]})
                from essence.backends.apde_router import APDERouter
                return APDERouter._default_provider(
                    messages, model, max_tokens, seed, call_class)

        return _real_provider
    except Exception as _e:
        log.debug("provider_chain_unavailable", extra={"reason": str(_e)[:120]})
        return None


def _detect_sandbox(workspace: "Path") -> bool:
    """
    Detect real container-sandbox availability.
    Returns True when a container runtime is present and Essence_CONTAINER=1.
    """
    try:
        from essence.security.sandbox import _EphemeralContainerSandbox
        cs = _EphemeralContainerSandbox(workspace)
        return cs.available()
    except Exception:
        return False


def boot_kernel(config_path: Path | None = None,
                provider_fn: "Any" = None,
                **overrides: Any) -> "Kernel":
    """
    Initialize the Essence kernel with all APDE capabilities live.
    Returns a Kernel with four public entry points.

    Args:
        config_path: Optional path to a TOML config file (unused at present;
                     reserved for future config-file support).
        provider_fn: Optional callable(messages, model, max_tokens, seed,
                     call_class) -> str.  When None, boot_kernel() tries to
                     build a real ProviderChain; if no backend is reachable it
                     falls back to the offline stub so boot always succeeds.
                     Pass a controlled stub explicitly in tests.
        **overrides: Keyword overrides for workspace, dev_mode, autonomy_tier,
                     plan/exec/verify_max_tokens, quota_limit.
    """
    # Step 1: Config
    ws = overrides.get("workspace", Path.home() / ".essence")
    if isinstance(ws, str):
        ws = Path(ws)
    ws.mkdir(parents=True, exist_ok=True)

    dev_mode      = overrides.get("dev_mode", True)
    autonomy_tier = overrides.get("autonomy_tier", 2)

    # Step 2: Runtime manifest
    manifest_path = overrides.get("manifest_path", None)
    manifest = RuntimeManifest.load(manifest_path, dev_mode=dev_mode)
    epoch_id = manifest.runtime_id

    log.info("boot_manifest_loaded", extra={
        "runtime_id": epoch_id,
        "dev_mode":   dev_mode,
    })

    # Step 3: Capsule Store migrations
    db_path = ws / "capsule_store.db"
    try:
        applied = apply_migrations(db_path)
    except Exception as _mig_exc:
        raise MigrationFailure(
            f"Capsule Store migration failed: {_mig_exc}") from _mig_exc
    if applied:
        log.info("boot_migrations_applied", extra={"applied": applied})
    capsule_repo = CapsuleRepository(db_path)
    plan_repo    = PlanRepository(db_path)
    delta_ledger = DeltaLedger(db_path)
    capsule_repo.assert_columns_exist()

    # Step 4: Tool Registry — unified source via build_tool_records()
    from essence.tools.registry import build_tool_records
    all_tool_records = build_tool_records()

    # Step 4b: Tool registry completeness check (boot failure policy)
    _REQUIRED_TOOL_IDS = {"shell", "read_file", "write_file", "python_exec", "web_search"}
    _registered_ids    = {t.tool_name for t in all_tool_records}
    _missing           = _REQUIRED_TOOL_IDS - _registered_ids
    if _missing:
        raise IncompleteToolRegistry(
            f"Tool registry missing required tools at boot: {sorted(_missing)}")

    # Step 4c: CapabilityDiscovery — hierarchical capability graph (Bug 9 fix)
    try:
        from essence.capability.discovery import CapabilityDiscovery
        capability_graph: Any = CapabilityDiscovery(workspace=ws)
        capability_graph.build_from_tool_records(all_tool_records)
        log.info("boot_capability_graph_built")
    except Exception as _cap_exc:
        log.debug("boot_capability_graph_unavailable",
                  extra={"reason": str(_cap_exc)[:80]})
        capability_graph = None

    # Step 5: Guardrail Layer — Fix 9: pass CostSQLite for quota persistence
    try:
        from essence.infra.cost_sqlite import CostSQLite
        # CostSQLite expects the workspace directory, not the db file path
        _quota_store: Any = CostSQLite(ws)
    except Exception as _cs_exc:
        log.debug("cost_sqlite_unavailable", extra={"reason": str(_cs_exc)[:80]})
        _quota_store = None

    guardrails = GuardrailLayer(
        quota_limit=overrides.get("quota_limit", 10000),
        quota_store=_quota_store,
    )

    # Step 5b: Wire real sandbox availability into G4 before smoke_test runs
    sandbox_available = _detect_sandbox(ws)
    guardrails.activate_sandbox(sandbox_available)
    if not sandbox_available:
        log.warning(
            "boot_sandbox_unavailable",
            extra={"detail": (
                "No container runtime found (docker/podman/nerdctl) or "
                "Essence_CONTAINER != 1. python_exec is running unsandboxed. "
                "Set Essence_CONTAINER=1 and install a container runtime for "
                "OS-level isolation."
            )},
        )

    guardrails.smoke_test()

    # Step 6: Resource Governor
    governor = ResourceGovernor(
        plan_max=overrides.get("plan_max_tokens",   8192),
        exec_max=overrides.get("exec_max_tokens",   16384),
        verify_max=overrides.get("verify_max_tokens", 4096),
    )

    # Step 6a: Resolve provider — caller-supplied > real chain > offline stub
    _resolved_provider = provider_fn
    if _resolved_provider is None:
        _resolved_provider = _build_provider_fn(ws)
        if _resolved_provider is not None:
            log.info("boot_provider_real_chain_active")
        else:
            log.info("boot_provider_offline_stub_active",
                     extra={"detail": "No backend reachable — using offline stub"})

    router = APDERouter(
        manifest=manifest,
        governor=governor,
        provider_fn=_resolved_provider,  # None → APDERouter uses its own stub
        epoch_id=epoch_id,
    )
    compressor = IntentCompressor(router, epoch_id)
    decomposer = Decomposer(router, epoch_id)

    # Step 6.2: Verification
    rubric_registry = RubricRegistry()
    assets_dir = Path(__file__).parent / "agents" / "assets"
    write_rubric_assets(assets_dir)
    rubric_registry.load_from_directory(assets_dir)
    apde_verifier = APDEVerifier(
        llm_router=router,
        rubric_registry=rubric_registry,
        guardrail_layer=guardrails,
        epoch_id=epoch_id,
    )

    # Step 6.3a: Wire bandit reward callback so rubric scores feed the
    # ContextualBanditRouter instead of being discarded (Issue 2 fix).
    # The bandit is optional — if it cannot be imported we skip silently
    # rather than blocking boot on a non-critical analytics component.
    try:
        from essence.backends.routing import ContextualBanditRouter, ABModelRouter
        _ab_router: Any = ABModelRouter(ws)
        _bandit: Any = ContextualBanditRouter(ws, _ab_router)
        apde_verifier.set_reward_callback(
            lambda model, score, ctx: _bandit.record(model, ctx, score)
        )
        log.info(
            "boot_bandit_reward_wired",
            extra={"detail": "APDEVerifier → ContextualBanditRouter.record()"},
        )
    except Exception as _bandit_exc:
        log.warning(
            "boot_bandit_unavailable — reward loop disabled",
            extra={"reason": str(_bandit_exc)[:120]},
        )

    # Step 6.3: Decision Guide — load directly; RuleLibrary.load() is fully implemented
    rule_lib   = RuleLibrary.load(ws)
    rule_index = RuleIndex(rule_lib.all_rules())
    rule_sel   = RuleSelector(rule_index)

    # Step 7: Signal Aggregator (SQE + TBC)
    stratum_store = StratumStore(db_path, epoch_id)
    sqe_sampler   = SQESampler(stratum_store)
    tbc           = TBCClassifier()
    sqe_sampler.on_regime_change(tbc.on_sqe_event)

    # Step 9: Context Window Manager
    ctx_mgr = ContextWindowManager()

    # Step 10: PIL/RIL Bridge
    ratifier   = Ratifier(autonomy_tier)
    staging    = StagingStore(str(ws / "staging"))
    pmp_history: list[IntentCapsule] = []
    pmp        = PMPPipeline(
        guardrail_layer=guardrails,
        delta_ledger=delta_ledger,
        capsule_history_ring=pmp_history,
        scratch_dir=str(ws / "scratch"),
    )

    # Step 11: Pipeline Executor — Fix 12: analytics wired conditionally
    try:
        from essence.analytics.engine import ANALYTICS_AVAILABLE, AnalyticalCore
        _analytics: Any = AnalyticalCore() if ANALYTICS_AVAILABLE else None
    except (ImportError, Exception):
        _analytics = None

    # Step 11b: Wire EventBus + SSEManager into executor (Parts 7/8)
    # Note: event_bus and sse_manager are wired in Step 12b/12c below;
    # we defer executor construction until after those steps so we can pass them.
    # Temporarily create a sentinel — executor will be re-created with wiring below.
    _executor_kwargs: dict = dict(
        llm_router=router,
        guardrail_layer=guardrails,
        scratch_dir=str(ws / "scratch"),
        analytics=_analytics,
    )
    executor = PipelineExecutor(**_executor_kwargs)

    # Step 15: Self-test
    _self_test(
        router=router,
        guardrails=guardrails,
        compressor=compressor,
        decomposer=decomposer,
        executor=executor,
        apde_verifier=apde_verifier,
        ctx_mgr=ctx_mgr,
        rule_sel=rule_sel,
        capsule_repo=capsule_repo,
        plan_repo=plan_repo,
        delta_ledger=delta_ledger,
        ratifier=ratifier,
    )

    try:
        from essence.security.audit_logger import AuditLogger
        audit_logger: Any = AuditLogger(db_path=ws / "audit.db")
        guardrails.set_audit_logger(audit_logger)
        log.info("boot_audit_logger_active",
                 extra={"db": str(ws / "audit.db")})
    except Exception as _al_exc:
        log.warning("boot_audit_logger_unavailable",
                    extra={"reason": str(_al_exc)[:120]})
        audit_logger = None

    try:
        from essence.infra.health import HealthMonitor
        import essence.infra.health as _health_mod
        _health_monitor = HealthMonitor()
        for _bn, _burl in getattr(router, "_provider_urls", {}).items():
            _health_monitor.register(_bn, _burl)
        _health_monitor.start(interval_s=60.0)
        _health_mod.HEALTH_MONITOR = _health_monitor
        log.info("boot_health_monitor_started")
    except Exception as _hm_exc:
        log.warning("boot_health_monitor_unavailable",
                    extra={"reason": str(_hm_exc)[:120]})

    try:
        from essence.routing import (
            IntentRouter, TaskRouter, EventBus, SubagentRouter,
        )
        from essence.routing.event_bus import set_event_bus
        intent_router: Any = IntentRouter(llm_router=router, workspace=ws)
        task_router:   Any = TaskRouter(workspace=ws)
        event_bus:     Any = EventBus()
        set_event_bus(event_bus)
        subagent_router: Any = SubagentRouter(workspace=ws)
        log.info("boot_routing_layer_active")
    except Exception as _rt_exc:
        log.warning("boot_routing_unavailable",
                    extra={"reason": str(_rt_exc)[:120]})
        intent_router = task_router = event_bus = subagent_router = None

    try:
        from essence.autonomy import GoalManager, CuriosityEngine
        from essence.agents.decision import DecisionQueue
        _decision_queue_boot: Any = DecisionQueue(ws)
        goal_manager: Any = GoalManager(
            kernel=None,
            decision_queue=_decision_queue_boot,
            audit_logger=audit_logger,
            workspace=ws,
        )
        curiosity_engine: Any = CuriosityEngine(goal_manager=goal_manager)
        log.info("boot_autonomy_layer_active")
    except Exception as _gm_exc:
        log.warning("boot_autonomy_unavailable",
                    extra={"reason": str(_gm_exc)[:120]})
        goal_manager = curiosity_engine = None

    try:
        from essence.server.sse_manager import get_sse_manager as _get_sse_mgr
        _sse_manager: Any = _get_sse_mgr()
    except Exception:
        _sse_manager = None

    executor = PipelineExecutor(
        llm_router=router,
        guardrail_layer=guardrails,
        scratch_dir=str(ws / "scratch"),
        analytics=_analytics,
        event_bus=event_bus,
        sse_manager=_sse_manager,
    )

    _hb_kernel_ref: list = []
    try:
        from essence.workspace.heartbeat import HeartbeatScheduler
        heartbeat: Any = HeartbeatScheduler(
            workspace=ws,
            run_fn=lambda msg: (
                _hb_kernel_ref[0](msg)
                if _hb_kernel_ref
                else "HEARTBEAT_OK:kernel_not_ready"
            ),
        )
        log.info("boot_heartbeat_instantiated")
    except Exception as _hb_exc:
        log.warning("boot_heartbeat_unavailable",
                    extra={"reason": str(_hb_exc)[:120]})
        heartbeat = None

    # ProactiveEngine
    try:
        from essence.agents.proactive import ProactiveEngine
        proactive: Any = ProactiveEngine(
            workspace=ws,
            memory=None,
            event_bus=event_bus,
        )
        log.info("boot_proactive_engine_active")
    except Exception as _pe_exc:
        log.debug("boot_proactive_unavailable",
                  extra={"reason": str(_pe_exc)[:80]})
        proactive = None

    # PersonalTwin
    try:
        from essence.identity.personal_twin import PersonalTwin
        twin: Any = PersonalTwin(ws)
        log.info("boot_twin_active")
    except Exception as _tw_exc:
        log.debug("boot_twin_unavailable", extra={"reason": str(_tw_exc)[:80]})
        twin = None

    # IntentEvolutionEngine
    try:
        from essence.autonomy.intent_evolution import IntentEvolutionEngine
        intent_evolution: Any = IntentEvolutionEngine(ws)
        log.info("boot_intent_evolution_active")
    except Exception as _ie_exc:
        log.debug("boot_intent_evolution_unavailable",
                  extra={"reason": str(_ie_exc)[:80]})
        intent_evolution = None

    # OpportunityEngine
    try:
        from essence.autonomy.opportunity_engine import OpportunityEngine
        opportunity_engine: Any = OpportunityEngine(ws)
        log.info("boot_opportunity_engine_active")
    except Exception as _oe_exc:
        log.debug("boot_opportunity_engine_unavailable",
                  extra={"reason": str(_oe_exc)[:80]})
        opportunity_engine = None

    # PersonalKnowledgeGraph
    try:
        from essence.memory.knowledge_graph import PersonalKnowledgeGraph
        kg: Any = PersonalKnowledgeGraph(ws)
        log.info("boot_knowledge_graph_active")
    except Exception as _kg_exc:
        log.debug("boot_knowledge_graph_unavailable",
                  extra={"reason": str(_kg_exc)[:80]})
        kg = None

    # DryRunSimulator
    try:
        from essence.simulation.dry_run import DryRunSimulator
        simulator: Any = DryRunSimulator()
        log.info("boot_dry_run_simulator_active")
    except Exception as _sim_exc:
        log.debug("boot_simulator_unavailable",
                  extra={"reason": str(_sim_exc)[:80]})
        simulator = None

    # UserStateDetector
    try:
        from essence.intelligence.state_detector import UserStateDetector
        state_detector: Any = UserStateDetector()
        log.info("boot_state_detector_active")
    except Exception as _sd_exc:
        log.debug("boot_state_detector_unavailable",
                  extra={"reason": str(_sd_exc)[:80]})
        state_detector = None

    # PersonalTrustLedger
    try:
        from essence.identity.trust_ledger import PersonalTrustLedger
        trust_ledger: Any = PersonalTrustLedger(ws)
        log.info("boot_trust_ledger_active")
    except Exception as _tl_exc:
        log.debug("boot_trust_ledger_unavailable",
                  extra={"reason": str(_tl_exc)[:80]})
        trust_ledger = None

    # ModelFabric
    try:
        from essence.backends.model_fabric import ModelFabric
        model_fabric: Any = ModelFabric(ws)
        log.info("boot_model_fabric_active")
    except Exception as _mf_exc:
        log.debug("boot_model_fabric_unavailable",
                  extra={"reason": str(_mf_exc)[:80]})
        model_fabric = None

    # WisdomEngine
    try:
        from essence.intelligence.wisdom_engine import WisdomEngine
        wisdom: Any = WisdomEngine(twin=twin)
        log.info("boot_wisdom_engine_active")
    except Exception as _we_exc:
        log.debug("boot_wisdom_engine_unavailable",
                  extra={"reason": str(_we_exc)[:80]})
        wisdom = None

    # MetaReflectionEngine
    try:
        from essence.workspace.skills.evolution.meta_reflect import MetaReflectionEngine
        meta_reflector: Any = MetaReflectionEngine(ws, twin=twin)
        log.info("boot_meta_reflector_active")
    except Exception as _mr_exc:
        log.debug("boot_meta_reflector_unavailable",
                  extra={"reason": str(_mr_exc)[:80]})
        meta_reflector = None

    # TemporalCognitionPlane
    try:
        from essence.intelligence.temporal_plane import TemporalCognitionPlane
        temporal: Any = TemporalCognitionPlane(ws)
        log.info("boot_temporal_plane_active")
    except Exception as _tp_exc:
        log.debug("boot_temporal_plane_unavailable",
                  extra={"reason": str(_tp_exc)[:80]})
        temporal = None

    # AutonomousResearchEngine
    try:
        from essence.autonomy.research_engine import AutonomousResearchEngine
        research_engine: Any = AutonomousResearchEngine(ws, twin=twin, router=router)
        log.info("boot_research_engine_active")
    except Exception as _re_exc:
        log.debug("boot_research_engine_unavailable",
                  extra={"reason": str(_re_exc)[:80]})
        research_engine = None

    # MemoryLifecycleManager — wire real episodic and semantic stores
    _episodic_store: Any = None
    _semantic_store: Any = None
    try:
        from essence.memory.episodic import EpisodicStore as _EpisodicStore
        _episodic_store = _EpisodicStore(ws)
    except Exception:
        pass
    try:
        from essence.memory.semantic_state import SemanticStateStore as _SemanticStore
        _semantic_store = _SemanticStore(ws)
    except Exception:
        pass
    try:
        from essence.memory.lifecycle import MemoryLifecycleManager
        memory_lifecycle: Any = MemoryLifecycleManager(
            ws, episodic_store=_episodic_store, semantic_store=_semantic_store, kg=kg)
        log.info("boot_memory_lifecycle_active")
    except Exception as _ml_exc:
        log.debug("boot_memory_lifecycle_unavailable",
                  extra={"reason": str(_ml_exc)[:80]})
        memory_lifecycle = None

    # AttentionManager
    try:
        from essence.attention.manager import AttentionManager
        attention: Any = AttentionManager(ws)
        log.info("boot_attention_manager_active")
    except Exception as _am_exc:
        log.debug("boot_attention_manager_unavailable",
                  extra={"reason": str(_am_exc)[:80]})
        attention = None

    # ObjectiveReconstructor
    try:
        from essence.intelligence.objective_reconstructor import ObjectiveReconstructor
        objective_reconstructor: Any = ObjectiveReconstructor(
            llm_router=router, twin=twin, temporal=temporal)
        log.info("boot_objective_reconstructor_active")
    except Exception as _or_exc:
        log.debug("boot_objective_reconstructor_unavailable",
                  extra={"reason": str(_or_exc)[:80]})
        objective_reconstructor = None

    # CognitiveHealthMonitor
    try:
        from essence.intelligence.cognitive_health import CognitiveHealthMonitor
        cognitive_health: Any = CognitiveHealthMonitor(
            twin=twin, temporal=temporal, trust_ledger=trust_ledger,
            kg=kg, sqe_sampler=sqe_sampler)
        log.info("boot_cognitive_health_monitor_active")
    except Exception as _ch_exc:
        log.debug("boot_cognitive_health_monitor_unavailable",
                  extra={"reason": str(_ch_exc)[:80]})
        cognitive_health = None

    # CapabilityRetirementManager
    try:
        from essence.capability.retirement import CapabilityRetirementManager
        capability_retirement: Any = CapabilityRetirementManager(ws)
        log.info("boot_capability_retirement_active")
    except Exception as _cr_exc:
        log.debug("boot_capability_retirement_unavailable",
                  extra={"reason": str(_cr_exc)[:80]})
        capability_retirement = None

    # UserPreferenceEngine — learns communication style / format from every interaction
    try:
        from essence.intelligence.user_preference_engine import UserPreferenceEngine
        user_preference_engine: Any = UserPreferenceEngine(ws)
        log.info("boot_user_preference_engine_active")
    except Exception as _upe_exc:
        log.debug("boot_user_preference_engine_unavailable",
                  extra={"reason": str(_upe_exc)[:80]})
        user_preference_engine = None

    # ── Wave 2 Intelligence Modules ──────────────────────────────────────────

    # ReminderEngine — schedules and fires goal-linked time-based reminders
    try:
        from essence.intelligence.reminder_engine import ReminderEngine
        reminder_engine: Any = ReminderEngine(
            workspace=ws, event_bus=event_bus, proactive=proactive)
        if temporal is not None:
            reminder_engine.sync_from_goals(temporal)
        log.info("boot_reminder_engine_active")
    except Exception as _re2_exc:
        log.debug("boot_reminder_engine_unavailable",
                  extra={"reason": str(_re2_exc)[:80]})
        reminder_engine = None

    # DailyBriefingEngine — personalised morning digest
    try:
        from essence.intelligence.daily_briefing import (
            DailyBriefingEngine, register_briefing_job,
        )
        daily_briefing: Any = DailyBriefingEngine(
            workspace        = ws,
            temporal         = temporal,
            twin             = twin,
            cognitive_health = cognitive_health,
            user_preference  = user_preference_engine,
            opportunity      = opportunity_engine,
            reminder         = reminder_engine,
            research         = research_engine,
            event_bus        = event_bus,
        )
        if heartbeat is not None:
            register_briefing_job(heartbeat, daily_briefing, ws)
        log.info("boot_daily_briefing_active")
    except Exception as _db_exc:
        log.debug("boot_daily_briefing_unavailable",
                  extra={"reason": str(_db_exc)[:80]})
        daily_briefing = None

    # EmotionalStateTracker — infers user sentiment for tone-adaptive responses
    try:
        from essence.intelligence.emotional_state import EmotionalStateTracker
        emotional_state: Any = EmotionalStateTracker(ws)
        log.info("boot_emotional_state_tracker_active")
    except Exception as _es_exc:
        log.debug("boot_emotional_state_tracker_unavailable",
                  extra={"reason": str(_es_exc)[:80]})
        emotional_state = None

    # KnowledgeGapDetector — fills PersonalTwin gaps via targeted questions
    try:
        from essence.intelligence.knowledge_gap_detector import KnowledgeGapDetector
        knowledge_gap_detector: Any = KnowledgeGapDetector(
            workspace=ws, twin=twin, event_bus=event_bus)
        log.info("boot_knowledge_gap_detector_active")
    except Exception as _kgd_exc:
        log.debug("boot_knowledge_gap_detector_unavailable",
                  extra={"reason": str(_kgd_exc)[:80]})
        knowledge_gap_detector = None

    # SessionReplayEngine — structured session snapshots with replay/branch
    try:
        from essence.intelligence.session_replay import SessionReplayEngine
        session_replay: Any = SessionReplayEngine(
            workspace    = ws,
            episodic     = _episodic_store,
            capsule_repo = capsule_repo,
        )
        session_replay.open_session()
        log.info("boot_session_replay_active")
    except Exception as _sr_exc:
        log.debug("boot_session_replay_unavailable",
                  extra={"reason": str(_sr_exc)[:80]})
        session_replay = None

    # LearningCurveTracker — tracks skill growth and surfaces milestones
    try:
        from essence.intelligence.learning_curve import LearningCurveTracker
        learning_curve: Any = LearningCurveTracker(
            workspace  = ws,
            twin       = twin,
            research   = research_engine,
            event_bus  = event_bus,
        )
        log.info("boot_learning_curve_tracker_active")
    except Exception as _lc_exc:
        log.debug("boot_learning_curve_tracker_unavailable",
                  extra={"reason": str(_lc_exc)[:80]})
        learning_curve = None

    sub = KernelSubsystems(
        manifest=manifest,
        governor=governor,
        router=router,
        guardrails=guardrails,
        compressor=compressor,
        decomposer=decomposer,
        executor=executor,
        apde_verifier=apde_verifier,
        rubric_registry=rubric_registry,
        rule_selector=rule_sel,
        ctx_mgr=ctx_mgr,
        capsule_repo=capsule_repo,
        plan_repo=plan_repo,
        delta_ledger=delta_ledger,
        ratifier=ratifier,
        pmp=pmp,
        sqe_sampler=sqe_sampler,
        tbc=tbc,
        all_tool_records=all_tool_records,
        autonomy_tier=autonomy_tier,
        epoch_id=epoch_id,
        intent_router=intent_router,
        task_router=task_router,
        event_bus=event_bus,
        goal_manager=goal_manager,
        audit_logger=audit_logger,
        curiosity_engine=curiosity_engine,
        subagent_router=subagent_router,
        staging=staging,
        heartbeat=heartbeat,
        proactive=proactive,
        capability_graph=capability_graph,
        twin=twin,
        intent_evolution=intent_evolution,
        opportunity_engine=opportunity_engine,
        kg=kg,
        simulator=simulator,
        state_detector=state_detector,
        trust_ledger=trust_ledger,
        model_fabric=model_fabric,
        wisdom=wisdom,
        meta_reflector=meta_reflector,
        temporal=temporal,
        research_engine=research_engine,
        memory_lifecycle=memory_lifecycle,
        attention=attention,
        objective_reconstructor=objective_reconstructor,
        cognitive_health=cognitive_health,
        capability_retirement=capability_retirement,
        user_preference_engine=user_preference_engine,
        reminder_engine        = reminder_engine,
        daily_briefing         = daily_briefing,
        emotional_state        = emotional_state,
        knowledge_gap_detector = knowledge_gap_detector,
        session_replay         = session_replay,
        learning_curve         = learning_curve,
        skill_system           = None,   # filled below after sub is created
    )

    # ── Skill System ──────────────────────────────────────────────────────────
    try:
        from essence.skills import boot_skill_system
        _mcp_clients = []
        try:
            from essence.tools.mcp import _STDIO_CLIENTS
            _mcp_clients = list(_STDIO_CLIENTS.values())
        except Exception:
            pass
        try:
            from essence.tools.registry import TOOL_REGISTRY as _skill_tr
        except Exception:
            _skill_tr = None
        sub.skill_system = boot_skill_system(
            workspace     = ws,
            router        = router,
            tool_registry = _skill_tr,
            event_bus     = event_bus,
            mcp_clients   = _mcp_clients,
        )
        # Register the 6 skill agent tools into the tool registry
        try:
            from essence.skills.tools import register_skill_tools
            if _skill_tr is not None:
                register_skill_tools(_skill_tr, sub.skill_system)
        except Exception as _skt_exc:
            log.debug("boot_skill_tools_registration_failed",
                      extra={"reason": str(_skt_exc)[:80]})
        log.info("boot_skill_system_active",
                 extra={"skills": sub.skill_system.repository.count()})
    except Exception as _ss_exc:
        log.debug("boot_skill_system_unavailable",
                  extra={"reason": str(_ss_exc)[:120]})
        sub.skill_system = None

    # MetaOrchestrator must be instantiated after sub so it receives the full object
    try:
        from essence.core.meta_orchestrator import MetaOrchestrator
        sub.meta = MetaOrchestrator(sub)
        log.info("boot_meta_orchestrator_active")
    except Exception as _mo_exc:
        log.debug("boot_meta_orchestrator_unavailable",
                  extra={"reason": str(_mo_exc)[:80]})

    kernel = Kernel(sub)
    if sub.heartbeat is not None:
        _hb_kernel_ref.append(
            lambda msg: kernel.ingest_capsule(msg, user_id="system:heartbeat")
        )
        if getattr(sub, "meta", None) is not None:
            sub.heartbeat.attach_meta(sub.meta)
        sub.heartbeat.start()
        log.info("boot_heartbeat_started")

    return kernel


def _self_test(router: Any, guardrails: Any, compressor: Any,
               decomposer: Any, executor: Any, apde_verifier: Any,
               ctx_mgr: Any, rule_sel: Any, capsule_repo: Any,
               plan_repo: Any, delta_ledger: Any, ratifier: Any) -> None:
    """Boot self-test — raises SelfTestFailure on any failure."""
    try:
        from essence.apde_types import (
            IntentCapsule, Task, PlanDAG, ExecResult,
            TaskState, RiskLevel, GuidanceBlock,
        )
        from essence.infra.context_view import ContextWindowManager
        from essence.tools.tool_belt import ToolBelt
        import hashlib, uuid as _uuid

        # Build minimal capsule
        capsule = IntentCapsule(
            id="selftest-capsule-001",
            raw_prompt="Write a file hello.txt and assert it exists.",
            goal="write scratch/hello.txt",
            success_signals=["scratch/hello.txt written"],
            artifacts=["scratch/hello.txt"],
            budget={"tokens": 512, "usd": 0.001},
            constraints=[],
            out_of_scope=[],
            lifecycle_state="draft",
            runtime_manifest_id=router._epoch_id,
            created_at=0.0,
        )
        capsule_repo.save(capsule)

        task = Task(
            id="selftest-task-001",
            capsule_id=capsule.id,
            goal="write scratch/hello.txt",
            reads=[],
            writes=["scratch/hello.txt"],
            tools=["write_file"],
            done_when="task completed",
            risk=RiskLevel.LOW,
        )

        plan = PlanDAG(
            id="selftest-plan-001",
            capsule_id=capsule.id,
            tasks=[task],
            runtime_manifest_id=router._epoch_id,
        )
        plan.freeze()
        plan_repo.save(plan)

        # Ratify
        rat = ratifier.ratify(plan)
        if not rat.approved:
            raise SelfTestFailure("Self-test: ratification denied")

        view = ctx_mgr.resolve(task)
        artifacts = delta_ledger.list_artifacts(capsule.id)
        if artifacts:
            view.load(artifacts)

        # ToolBelt
        from essence.apde_types import ToolRecord
        records = [ToolRecord("write_file", ["write"], ["G3"], "MEDIUM", 20, False)]
        belt    = ToolBelt(records)

        # GuidanceBlock
        gb = rule_sel.build_guidance(task)

        result = executor.execute(task, view, belt, gb)
        assert result.state in (TaskState.DONE, TaskState.DONE_INSUFFICIENT), (
            f"Self-test: unexpected task state {result.state}")

        for artifact_path in result.artifacts:
            delta_ledger.record_artifact(capsule.id, artifact_path, {"path": artifact_path})

        # Verify
        outcome = apde_verifier.verify(task, result)

        # Guardrail audit
        guardrails.audit("self_test_complete", {
            "task_id": task.id, "passed": True,
            "score": outcome.score,
        })

        log.info("boot_self_test_passed")

    except SelfTestFailure:
        raise
    except Exception as e:
        raise SelfTestFailure(f"Boot self-test failed: {e}") from e


class Kernel:
    """
    Kernel facade. Exposes exactly four public entry points:
    ingest_capsule, user_input, tick, audit.
    """

    def __init__(self, sub: KernelSubsystems) -> None:
        self._s = sub
        if self._s.goal_manager is not None:
            self._s.goal_manager._kernel = self

    @property
    def manifest(self) -> Any:
        """Expose the RuntimeManifest for API/server introspection."""
        return self._s.manifest

    def ingest_capsule(self, raw_prompt: str, user_id: str,
                       autonomy_tier: int = 2) -> str:
        """
        Convert raw_prompt → frozen PlanDAG → capsule_id.
        Stages A (compress), B (decompose), C (ratify).

        Args:
            raw_prompt:    The user's raw intent string.
            user_id:       Identifier used for quota tracking.
            autonomy_tier: Override the kernel autonomy tier for this capsule.

        Returns:
            The capsule_id (a uuid string) for use in tick() and user_input().

        Raises:
            GuardrailDenied: if pre_plan guardrail G1/G2 fires.
            GuardrailDenied: if ratification fails (plan ratification denied).
        """
        guardrails = self._s.guardrails
        compressor = self._s.compressor
        decomposer = self._s.decomposer
        ratifier   = self._s.ratifier
        repo       = self._s.capsule_repo
        plan_repo  = self._s.plan_repo
        epoch_id   = self._s.epoch_id

        guardrails.pre_plan(raw_prompt, user_id)

        if self._s.user_preference_engine is not None:
            try:
                self._s.user_preference_engine.observe(raw_prompt, user_id=user_id)
            except Exception as _upe_obs_exc:
                log.debug("user_preference_observe_error",
                          extra={"error": str(_upe_obs_exc)[:80]})

        # EmotionalStateTracker — update sentiment model from this message
        if self._s.emotional_state is not None and not user_id.startswith("system:"):
            try:
                self._s.emotional_state.observe(raw_prompt)
            except Exception as _est_exc:
                log.debug("emotional_state_observe_error",
                          extra={"error": str(_est_exc)[:80]})

        # KnowledgeGapDetector — try to confirm any pending gap question
        if self._s.knowledge_gap_detector is not None and not user_id.startswith("system:"):
            try:
                self._s.knowledge_gap_detector.confirm(raw_prompt)
            except Exception as _kgd_exc:
                log.debug("knowledge_gap_confirm_error",
                          extra={"error": str(_kgd_exc)[:80]})

        # LearningCurveTracker — observe domains from user preference engine
        if (self._s.learning_curve is not None
                and self._s.user_preference_engine is not None
                and not user_id.startswith("system:")):
            try:
                _domains = self._s.user_preference_engine.profile().top_domains(5)
                if _domains:
                    self._s.learning_curve.observe_domains(_domains)
            except Exception as _lc_exc:
                log.debug("learning_curve_observe_error",
                          extra={"error": str(_lc_exc)[:80]})

        # SessionReplayEngine — record this message as a session step
        if self._s.session_replay is not None and not user_id.startswith("system:"):
            try:
                self._s.session_replay.record_step(
                    kind    = "user_message",
                    content = raw_prompt[:300],
                )
            except Exception as _sre_exc:
                log.debug("session_replay_record_error",
                          extra={"error": str(_sre_exc)[:80]})

        if self._s.objective_reconstructor is not None:
            try:
                _rec = self._s.objective_reconstructor.reconstruct(raw_prompt)
                if _rec.confidence > 0.75 and _rec.inferred_objective != raw_prompt:
                    raw_prompt = (
                        f"{raw_prompt}\n"
                        f"[UNDERLYING OBJECTIVE: {_rec.inferred_objective}]"
                    )
                    log.debug("objective_reconstructed", extra={
                        "literal":    raw_prompt[:60],
                        "objective":  _rec.inferred_objective[:60],
                    })
            except Exception as _rec_exc:
                log.debug("objective_reconstructor_error",
                          extra={"error": str(_rec_exc)[:80]})

        _enriched_prompt = raw_prompt
        if getattr(self._s, "meta", None) is not None:
            try:
                _ctx_prefix = self._s.meta.enrich_prompt_context(raw_prompt)
                if _ctx_prefix:
                    _enriched_prompt = f"{_ctx_prefix}\n\n{raw_prompt}"
                    log.debug("meta_orchestrator_enriched",
                              extra={"prefix_len": len(_ctx_prefix)})
            except Exception as _enrich_exc:
                log.debug("meta_orchestrator_enrich_error",
                          extra={"error": str(_enrich_exc)[:80]})
        raw_prompt = _enriched_prompt

        _intent: Any = None
        if self._s.intent_router is not None:
            try:
                import asyncio as _asyncio
                _intent = _asyncio.run(
                    self._s.intent_router.route(raw_prompt, session_id=user_id)
                )
                if self._s.intent_evolution is not None and _intent is not None:
                    try:
                        _intent_type = getattr(
                            getattr(_intent, "type", None), "value", "")
                        if _intent_type:
                            self._s.intent_evolution.record(_intent_type)
                    except Exception:
                        pass
            except Exception as _ir_exc:
                log.debug("intent_router_error", extra={"error": str(_ir_exc)[:80]})

        capsule = compressor.compress(
            raw_prompt, runtime_manifest_id=epoch_id, intent=_intent)
        capsule.lifecycle_state = "draft"
        repo.save(capsule)

        plan = decomposer.decompose(capsule, runtime_manifest_id=epoch_id)
        plan_repo.save(plan)

        rat = ratifier.ratify(plan)
        if not rat.approved:
            from essence.apde_types import GuardrailDenied
            raise GuardrailDenied("ratification", "Plan ratification denied")

        from essence.apde_types import PlanStatus
        plan.transition(PlanStatus.ACTIVE)
        plan_repo.save(plan)

        return capsule.id

    def user_input(self, capsule_id: str, raw_input: str,
                   user_id: str = "user") -> dict:
        """
        Process follow-up user input against an existing capsule via PMP.

        The follow-up prompt is routed through IntentCompressor.compress()
        to produce a properly structured IntentCapsule before the PMP diff runs.
        Plan lookup uses plan_repo.get_active_plan(capsule_id).

        Returns:
            dict with keys: event_id, mutation_class, action, summary.
        """
        repo       = self._s.capsule_repo
        plan_repo  = self._s.plan_repo
        pmp        = self._s.pmp
        compressor = self._s.compressor

        old_capsule = repo.get(capsule_id)
        if old_capsule is None:
            raise KeyError(f"Capsule {capsule_id} not found")

        new_capsule = compressor.compress(
            raw_prompt=raw_input,
            runtime_manifest_id=old_capsule.runtime_manifest_id,
        )
        new_capsule.id = f"{capsule_id}_follow_{int(time.time())}"

        current_plan = plan_repo.get_active_plan(capsule_id)
        if current_plan is None:
            from essence.apde_types import PlanDAG
            current_plan = PlanDAG(id="", capsule_id=capsule_id, tasks=[])

        pmp_result = pmp.run(old_capsule, new_capsule, current_plan)
        repo.save(new_capsule)

        return {
            "event_id":       pmp_result.event_id,
            "mutation_class": pmp_result.mutation_class,
            "action":         pmp_result.action,
            "summary":        pmp_result.summary,
        }

    def tick(self, capsule_id: str) -> dict:
        """
        Advance one task in the plan: READY → ACTIVE → execute → verify.

        Returns:
            dict with keys: task_id, state, tokens — or status/capsule_id on
            no-plan / no-ready-tasks conditions.
        """
        plan_repo    = self._s.plan_repo
        capsule_repo = self._s.capsule_repo
        executor     = self._s.executor
        apde_verifier = self._s.apde_verifier
        ctx_mgr      = self._s.ctx_mgr
        rule_sel     = self._s.rule_selector
        sqe          = self._s.sqe_sampler
        all_tools    = self._s.all_tool_records
        delta_ledger = self._s.delta_ledger

        plan = plan_repo.get_active_plan(capsule_id)
        if plan is None:
            return {"status": "no_plan", "capsule_id": capsule_id}

        ready = [t for t in plan.tasks if t.state == TaskState.READY]
        if not ready:
            return {"status": "no_ready_tasks", "plan_id": plan.id}

        task = ready[0]
        plan_repo.update_task_state(plan.id, task.id, TaskState.ACTIVE)

        if self._s.attention is not None:
            try:
                self._s.attention.focus(task.goal, source="tick", capsule_id=capsule_id)
            except Exception:
                pass

        view = ctx_mgr.resolve(task)
        artifacts = delta_ledger.list_artifacts(capsule_id)
        if artifacts:
            view.load(artifacts)

        belt = ToolBelt(all_tools)
        if rule_sel:
            gb = rule_sel.build_guidance(task)
        else:
            from essence.apde_types import GuidanceBlock
            gb = GuidanceBlock(rules=[], risk=task.risk)

        if getattr(self._s, "meta", None) is not None:
            try:
                _task_descs = [task.goal]
                _gate = self._s.meta.pre_execution_gate(
                    plan_summary=task.goal,
                    task_descriptions=_task_descs,
                    action_class=getattr(task, "skill", "general") or "general",
                )
                if not _gate.get("proceed", True):
                    log.warning("meta_orchestrator_gate_blocked", extra={
                        "task_id": task.id,
                        "reason":  _gate.get("reason", "")[:120],
                        "escalate": _gate.get("escalate", False),
                    })
                    from essence.apde_types import TaskState as _TS2
                    plan_repo.update_task_state(plan.id, task.id, _TS2.DONE_INSUFFICIENT)
                    return {
                        "task_id": task.id,
                        "state":   "DONE_INSUFFICIENT",
                        "tokens":  0,
                        "blocked_by": "meta_orchestrator",
                        "reason":  _gate.get("reason", ""),
                    }
            except Exception as _gate_exc:
                log.debug("meta_orchestrator_gate_error",
                          extra={"error": str(_gate_exc)[:80]})

        result = executor.execute(task, view, belt, gb)
        plan_repo.update_task_state(plan.id, task.id, result.state)

        for artifact_path in result.artifacts:
            delta_ledger.record_artifact(
                capsule_id, artifact_path, {"path": artifact_path})

        # Verify
        capsule = capsule_repo.get(capsule_id)
        if capsule:
            outcome = apde_verifier.verify(task, result)
            sqe.record(task, outcome)

        if getattr(self._s, "meta", None) is not None:
            try:
                _accepted = result.state.value not in ("DONE_INSUFFICIENT", "ERROR", "CANCELLED")
                _episode = {
                    "skill":      getattr(task, "skill", task.goal[:40]),
                    "task_id":    task.id,
                    "capsule_id": capsule_id,
                    "state":      result.state.value,
                    "tokens":     result.token_usage,
                    "artifacts":  result.artifacts,
                }
                self._s.meta.post_execution_learn(_episode, _accepted)
            except Exception as _learn_exc:
                log.debug("meta_orchestrator_learn_error",
                          extra={"error": str(_learn_exc)[:80]})

        if self._s.goal_manager is not None:
            try:
                autonomous_goals = self._s.goal_manager.drain_autonomous()
                for _goal in autonomous_goals:
                    try:
                        _auto_cid = self.ingest_capsule(
                            raw_prompt=f"{_goal.skill}: {_goal.params}",
                            user_id="system:autonomy",
                            autonomy_tier=3,
                        )
                        log.info("autonomous_goal_ingested",
                                 extra={"goal_id": _goal.id, "capsule_id": _auto_cid})
                    except Exception as _goal_exc:
                        log.warning("autonomous_goal_ingest_failed",
                                    extra={"goal_id": _goal.id,
                                           "error": str(_goal_exc)[:120]})
            except Exception:
                pass

        if self._s.event_bus is not None:
            try:
                self._s.event_bus.publish_sync("task.complete", {
                    "task_id":    task.id,
                    "capsule_id": capsule_id,
                    "state":      result.state.value,
                })
            except Exception:
                pass

        return {
            "task_id": task.id,
            "state":   result.state.value,
            "tokens":  result.token_usage,
        }

    def audit(self) -> list[dict]:
        """Return the full guardrail audit trail."""
        return self._s.guardrails.get_audit_trail()


# ── Boot entry point with process-exit contract (boot failure policy) ──────────
_FATAL_BOOT_ERRORS = (
    SelfTestFailure,
    MigrationFailure,
    IncompleteToolRegistry,
    RetiredSubsystemError,
    ImportError,
)

def boot_main(config_path: str | None = None, **overrides) -> "Kernel":
    """
    Wrapper around boot_kernel() that enforces sys.exit(1) for the five fatal
    error types mandated by the boot-failure policy (spec boot kernel section).
    """
    try:
        return boot_kernel(config_path, **overrides)
    except _FATAL_BOOT_ERRORS as exc:
        import traceback
        traceback.print_exc()
        _log = logging.getLogger("essence.boot")
        _log.critical("boot_fatal",
                      extra={"error_type": type(exc).__name__, "detail": str(exc)})
        sys.exit(1)
