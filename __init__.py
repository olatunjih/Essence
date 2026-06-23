"""
Essence — Agentic Intelligence System

An autonomous agent kernel: plan → decompose → execute → verify loop
wrapped in production-grade infrastructure (auth, rate limiting, circuit
breakers, observability, multi-backend LLM routing, vector memory, a
human-ratification pipeline, and a FastAPI server/UI).

Public entry points (via boot_kernel → Kernel):
    Kernel.ingest_capsule(raw_prompt, user_id)  → capsule_id
    Kernel.user_input(capsule_id, raw_input)    → mutation result
    Kernel.tick(capsule_id)                     → execution status
    Kernel.audit()                              → audit trail

Fix 7: __init__.py now uses narrow explicit exports instead of eagerly
loading 170+ submodules and merging all their globals into the package
namespace.  Only the stable public API is re-exported here.
"""
from __future__ import annotations

from essence._shared import ESSENCE_VERSION, ESSENCE_BUILD  # noqa: F401
from essence.apde_types import (  # noqa: F401
    CallClass,
    TaskState,
    PlanStatus,
    Severity,
    RiskLevel,
    APDEError,
    RetiredSubsystemError,
    ManifestVerificationError,
    AnchorDriftError,
    GuardrailDenied,
    AxiomViolation,
    SelfTestFailure,
    MigrationFailure,
    IncompleteToolRegistry,
    LegacyImportError,
    ContextScopeError,
    ResourceGovernorError,
    IntentCapsule,
    Task,
    PlanDAG,
    ExecResult,
    VerificationOutcome,
    GuidanceBlock,
    ContextView,
    ToolRecord,
    TokenGrant,
    PlanDeltaRow,
    APDE_NAMESPACE,
)
from essence.boot import boot_kernel, boot_main, Kernel, KernelSubsystems  # noqa: F401

__all__ = [
    # Version
    "ESSENCE_VERSION",
    "ESSENCE_BUILD",
    # Enums
    "CallClass",
    "TaskState",
    "PlanStatus",
    "Severity",
    "RiskLevel",
    # Exceptions
    "APDEError",
    "RetiredSubsystemError",
    "ManifestVerificationError",
    "AnchorDriftError",
    "GuardrailDenied",
    "AxiomViolation",
    "SelfTestFailure",
    "MigrationFailure",
    "IncompleteToolRegistry",
    "LegacyImportError",
    "ContextScopeError",
    "ResourceGovernorError",
    # Core dataclasses
    "IntentCapsule",
    "Task",
    "PlanDAG",
    "ExecResult",
    "VerificationOutcome",
    "GuidanceBlock",
    "ContextView",
    "ToolRecord",
    "TokenGrant",
    "PlanDeltaRow",
    "APDE_NAMESPACE",
    # Kernel
    "boot_kernel",
    "boot_main",
    "Kernel",
    "KernelSubsystems",
]
