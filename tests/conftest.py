"""Shared pytest fixtures for the Essence test suite.

 fix: explicit imports for every class name the tests expect.
The previous `from essence import *` relied on a wildcard-heavy __init__.py
that was deliberately narrowed. This file now imports each name from its
real module so pytest NameErrors are eliminated without reverting __init__.py.
"""
# ruff: noqa
# fmt: off
from __future__ import annotations

import sys
import unittest.mock as _mock  # noqa: F401
import essence as _essence
sys.modules.setdefault("essence", _essence)

import pytest  # type: ignore
from essence._shared import *  # noqa: F401,F403

# ── Core types ─────────────────────────────────────────────────────────────
from essence.apde_types import (  # noqa: F401
    Task, TaskState, PlanDAG, GuidanceBlock, CallClass, RiskLevel,
    GuardrailDenied, AxiomViolation, RetiredSubsystemError,
    ExecResult,
)

# ── Memory ──────────────────────────────────────────────────────────────────
from essence.memory.memory import Memory, NamespacedMemory          # noqa: F401
from essence.memory.episodic import EpisodicStore                   # noqa: F401
from essence.memory.migrator import MemoryMigrator                  # noqa: F401
from essence.memory.semantic_state import SemanticStateStore, SemanticFact  # noqa: F401
from essence.memory.backends import _JsonMemoryBackend              # noqa: F401
from essence.memory.team_sync import TeamMemorySync                 # noqa: F401

# ── Infra ───────────────────────────────────────────────────────────────────
from essence.infra.cost_sqlite import CostSQLite                    # noqa: F401
from essence.infra.context_view import ContextWindowManager         # noqa: F401
from essence.infra.budget import BudgetGuardedProvider              # noqa: F401
from essence.backends.routing import BudgetExceededError            # noqa: F401
from essence.infra.cache import SemanticResponseCache               # noqa: F401
from essence.infra.circuit import CircuitBreaker                    # noqa: F401
from essence.infra.limiter import ConcurrencyLimiter                # noqa: F401
from essence.infra.ratelimit import RateLimiter                     # noqa: F401
from essence.infra.dedup import RequestDeduplicator                 # noqa: F401
from essence.infra.retry_queue import RetryQueue                    # noqa: F401
from essence.infra.plugin import PluginLoader                       # noqa: F401
from essence.infra.schema import SchemaRegistry                     # noqa: F401
from essence.infra.health import HealthMonitor                      # noqa: F401
from essence.infra.duckdb import DuckDBAnalytics                    # noqa: F401
from essence.infra.export import WorkspaceExporter                  # noqa: F401
from essence.infra.migrate import WorkspaceMigrator                 # noqa: F401
try:
    from essence.infra.valkey import ValkeyRateLimiter              # noqa: F401
except ImportError:
    pass
try:
    from essence.infra.nats import NATSEventBus                     # noqa: F401
except ImportError:
    pass

# ── Security ────────────────────────────────────────────────────────────────
from essence.security.guardrail_layer import GuardrailLayer         # noqa: F401
from essence.security.sandbox import ProcessSandbox                  # noqa: F401
from essence.infra.sandbox2 import SandboxedExecutor                # noqa: F401
try:
    from essence.security.sandbox import SeccompSandbox             # noqa: F401
except ImportError:
    pass
from essence.core.vault import SecretsVault                         # noqa: F401
from essence.infra.auth import APIKeyStore                          # noqa: F401
from essence.infra.audit import AuditLog                            # noqa: F401
from essence.security.audit_logger import AuditLogger               # noqa: F401

# ── Backends ────────────────────────────────────────────────────────────────
from essence.backends.routing import ABModelRouter, ContextualBanditRouter  # noqa: F401
from essence.backends.apde_router import APDERouter                 # noqa: F401
from essence.backends.adapters import (                             # noqa: F401
    OllamaBackend, LiteLLMBackend, ProviderChain, BackendError,
)
from essence.core.registry import ModelSpec                         # noqa: F401
try:
    from essence.backends.adapters import LlamaCppPythonBackend, OnnxBackend  # noqa: F401
except ImportError:
    pass

# ── Agents ──────────────────────────────────────────────────────────────────
from essence.agents.agent import Agent, AgentConfig, AgentRole      # noqa: F401
from essence.agents.pipeline_executor import PipelineExecutor       # noqa: F401
from essence.agents.verifier import VerifierLayer, VerificationResult  # noqa: F401
from essence.agents.eval import EvalHarness, EvalScenario, EvalResult  # noqa: F401
from essence.agents.observer import AgentObserver                   # noqa: F401
from essence.agents.workflow import DAGWorkflowExecutor, DAGStep, WorkflowStep  # noqa: F401
from essence.infra.structured_log import WorkflowStepEvent          # noqa: F401
from essence.agents.proactive import ProactiveEngine                # noqa: F401

# ── Analytics ───────────────────────────────────────────────────────────────
from essence.analytics.spine import get_analytical_spine            # noqa: F401
from essence.analytics.experiment import ExperimentTracker          # noqa: F401
from essence.analytics.learning import LearningEngine               # noqa: F401
from essence.agents.critic import CriticResult                      # noqa: F401

# ── Workspace ────────────────────────────────────────────────────────────────
from essence.workspace.heartbeat import HeartbeatScheduler, HeartbeatJob  # noqa: F401
from essence.tools.mcp import SkillRunner                           # noqa: F401
from essence.capability.discovery import SelfEvolutionLoop          # noqa: F401
from essence.workspace.skills.evolution.switch import EvolutionMode # noqa: F401
try:
    from essence.workspace.skills.evolution.reflect import ReflectionSkill  # noqa: F401
    from essence.workspace.skills.evolution.switch import SkillEvolutionSwitch  # noqa: F401
    from essence.workspace.skills.evolution.propose import SkillProposer   # noqa: F401
    from essence.workspace.skills.evolution.patch import SkillPatcher      # noqa: F401
    from essence.workspace.skills.evolution.verify import SkillVerifier, SkillVerificationResult  # noqa: F401
except ImportError:
    pass
from essence.workspace.sop import SOPLoader                         # noqa: F401
from essence.workspace.ingestor import DocumentIngestor             # noqa: F401
try:
    from essence.workspace.benchmark import EvalHarness as _BenchEH  # noqa: F401  # alias
except ImportError:
    pass

# ── Channels ─────────────────────────────────────────────────────────────────
try:
    from essence.channels.telegram import TelegramAdapter           # noqa: F401
    from essence.channels.extended import DiscordAdapter, VoiceAdapter  # noqa: F401
    from essence.channels.bridge import ChannelAdapter              # noqa: F401
except ImportError:
    pass
try:
    from essence.tools.voice import VoicePipeline                   # noqa: F401
except ImportError:
    pass

# ── Routing / event bus ──────────────────────────────────────────────────────
from essence.routing.intent_router import IntentRouter              # noqa: F401
from essence.routing.task_router import TaskRouter                  # noqa: F401
from essence.routing.event_bus import EventBus                      # noqa: F401

# ── Tools ────────────────────────────────────────────────────────────────────
from essence.tools.registry import TOOL_REGISTRY as ToolRegistry   # noqa: F401
from essence.backends.routing import CostTracker                    # noqa: F401
from essence.apde_types import ToolRecord                           # noqa: F401
from essence.tools.tool_belt import ToolBelt                        # noqa: F401
from essence.tools.browser import BrowserSession                    # noqa: F401

# ── Protocols ────────────────────────────────────────────────────────────────
from essence.protocols.a2a import A2AClient, A2AServer, A2ATask    # noqa: F401

# ── Capability graph ──────────────────────────────────────────────────────────
from essence.capability.discovery import (                          # noqa: F401
    CapabilityGraph, CapabilityNode, CapabilityNodeType,
    CapabilityEdge, CapabilityEdgeType,
    ResolutionLevel, ProjectionFunction,
)
# Enum members from CapabilityNodeType
SKILL     = CapabilityNodeType.SKILL      # noqa: F401
TOOL      = CapabilityNodeType.TOOL       # noqa: F401
PARAMETER = CapabilityNodeType.PARAMETER  # noqa: F401
DOMAIN    = CapabilityNodeType.DOMAIN     # noqa: F401
CLUSTER   = CapabilityNodeType.CLUSTER    # noqa: F401
PHANTOM   = CapabilityNodeType.PHANTOM    # noqa: F401
# Enum members from CapabilityEdgeType
CONTAINS  = CapabilityEdgeType.CONTAINS   # noqa: F401
REQUIRES  = CapabilityEdgeType.REQUIRES   # noqa: F401
ENHANCES  = CapabilityEdgeType.ENHANCES   # noqa: F401
COMPOSES  = CapabilityEdgeType.COMPOSES   # noqa: F401
CONFLICTS = CapabilityEdgeType.CONFLICTS  # noqa: F401

# ── Server / OpenCanvas ───────────────────────────────────────────────────────
try:
    from essence.server.opencanvas import (                         # noqa: F401
        OCArtifact, OCArtifactStore, OCArtifactDetector,
    )
    from essence.server.opencanvas import ArtifactType as _OCType   # noqa: F401
except ImportError:
    pass

# ── Pipelines ─────────────────────────────────────────────────────────────────
try:
    from essence.pipelines.fusion import LLMCallEvent, ToolCallEvent  # noqa: F401
    from essence.pipelines.provenance import ProvenanceTracker      # noqa: F401
except ImportError:
    pass

# ── Inference / context ───────────────────────────────────────────────────────
try:
    from essence.backends.routing import ContextBudgetManager       # noqa: F401
    from essence.infra.token_count import BoundedTokenQueue         # noqa: F401
    from essence.memory.search import RelevanceScorer               # noqa: F401
    from essence.agents.intent import LanguageUnderstanding          # noqa: F401
    from essence.analytics.resilience import ResilienceLayer        # noqa: F401
    from essence.workspace.mesh import MeshNode                     # noqa: F401
    from essence.capability.discovery import AnticipationEngine      # noqa: F401
except ImportError:
    pass

# ── Hardware / Misc ───────────────────────────────────────────────────────────
try:
    from essence.core.hardware import HardwareProfile               # noqa: F401
    from essence.workspace.guided import EssenceConfig              # noqa: F401
    from essence.agents.decision import DecisionQueue, DecisionPriority  # noqa: F401
    from essence.channels.ratification import Ratifier              # noqa: F401  # alias
    from essence.agents.critic import RequestComplexity             # noqa: F401
    from essence.channels.ratification import Ratifier as _Rat2     # noqa: F401  # alias
except ImportError:
    pass


@pytest.fixture(autouse=True)
def _reset_ollama_discovery_guard():
    """`core.registry._ensure_ollama_models_discovered()` keeps its
    once-per-process guard as a real module global, not something tests can
    reach via `global _ollama_discovered` in their own module (that only
    rebinds the test module's own copy). Reset the real one around every
    test so tests that exercise discovery don't leak state into others."""
    import essence.core.registry as _registry
    previous = _registry._ollama_discovered
    _registry._ollama_discovered = False
    yield
    _registry._ollama_discovered = previous
