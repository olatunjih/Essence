
"""APDE shared types: enums, dataclasses, exceptions used across all subsystems."""
from __future__ import annotations
import dataclasses as _dc, enum as _enum, hashlib, json, time, uuid
from typing import Any

APDE_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


class CallClass(_enum.Enum):
    PLAN   = "PLAN"
    EXEC   = "EXEC"
    VERIFY = "VERIFY"


class TaskState(_enum.Enum):
    """NN-6: five-state lattice — no additional states permitted."""
    READY            = "READY"
    ACTIVE           = "ACTIVE"
    DONE             = "DONE"
    DONE_INSUFFICIENT = "DONE_INSUFFICIENT"
    FAILED           = "FAILED"

    @staticmethod
    def valid_transitions() -> dict[str, list[str]]:
        return {
            "READY":             ["ACTIVE"],
            "ACTIVE":            ["DONE", "DONE_INSUFFICIENT", "FAILED"],
            "DONE":              [],
            "DONE_INSUFFICIENT": ["ACTIVE"],
            "FAILED":            [],
        }

    def can_transition_to(self, target: "TaskState") -> bool:
        allowed = self.valid_transitions().get(self.value, [])
        return target.value in allowed


class PlanStatus(_enum.Enum):
    DRAFT    = "DRAFT"
    ACTIVE   = "ACTIVE"
    COMPLETE = "COMPLETE"
    ABORTED  = "ABORTED"

    @staticmethod
    def valid_transitions() -> dict[str, list[str]]:
        return {
            "DRAFT":    ["ACTIVE"],
            "ACTIVE":   ["COMPLETE", "ABORTED"],
            "COMPLETE": [],
            "ABORTED":  [],
        }

    def can_transition_to(self, target: "PlanStatus") -> bool:
        return target.value in self.valid_transitions().get(self.value, [])


class Severity(_enum.Enum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


class RiskLevel(_enum.Enum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


# ── Exceptions ────────────────────────────────────────────────────────────────

class APDEError(RuntimeError):
    """Base for all APDE errors."""

class RetiredSubsystemError(APDEError):
    """Raised when a retired legacy name is accessed."""

class ManifestVerificationError(APDEError):
    """Manifest signature, hash, or schema check failed."""

class AnchorDriftError(APDEError):
    """Anchor sha256 mismatch detected at boot."""

class GuardrailDenied(APDEError):
    """Raised when a guardrail denies an operation."""
    def __init__(self, guardrail_id: str, reason: str) -> None:
        super().__init__(f"Guardrail {guardrail_id} denied: {reason}")
        self.guardrail_id = guardrail_id
        self.reason = reason

class AxiomViolation(APDEError):
    """A planning or constitutional axiom was violated."""

class SelfTestFailure(APDEError):
    """Boot self-test did not pass."""

class MigrationFailure(APDEError):
    """Capsule Store schema migration failed at boot."""

class IncompleteToolRegistry(APDEError):
    """Tool registry is missing one or more required tools at boot."""

class LegacyImportError(APDEError):
    """Attempt to import a retired module by its old surface."""

class ContextScopeError(APDEError):
    """Out-of-scope context read rejected (Axiom A3)."""

class ResourceGovernorError(APDEError):
    """Token grant refused by the Resource Governor."""


# ── Core dataclasses ──────────────────────────────────────────────────────────

@_dc.dataclass
class IntentCapsule:
    id:             str
    raw_prompt:     str
    goal:           str
    success_signals: list[str]
    artifacts:      list[str]
    budget:         dict
    constraints:    list[str]   = _dc.field(default_factory=list)
    out_of_scope:   list[str]   = _dc.field(default_factory=list)
    apde_role:      str         = "intent"
    lifecycle_state: str        = "draft"
    runtime_manifest_id: str    = ""
    created_at:     float       = _dc.field(default_factory=time.time)

    def to_dict(self) -> dict:
        return _dc.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "IntentCapsule":
        return cls(**{k: v for k, v in d.items() if k in {f.name for f in _dc.fields(cls)}})


@_dc.dataclass
class Task:
    id:          str
    capsule_id:  str
    goal:        str
    reads:       list[str]      = _dc.field(default_factory=list)
    writes:      list[str]      = _dc.field(default_factory=list)
    tools:       list[str]      = _dc.field(default_factory=list)
    state:       TaskState      = TaskState.READY
    risk:        RiskLevel      = RiskLevel.LOW
    parent_id:   str            = ""
    subtask_ids: list[str]      = _dc.field(default_factory=list)
    done_when:   str            = ""
    result:      str            = ""
    token_usage: int            = 0

    def transition(self, target: TaskState) -> None:
        if not self.state.can_transition_to(target):
            raise AxiomViolation(
                f"Invalid task state transition: {self.state.value} -> {target.value}")
        self.state = target


@_dc.dataclass
class PlanDAG:
    id:           str
    capsule_id:   str
    tasks:        list[Task]    = _dc.field(default_factory=list)
    plan_hash:    str           = ""
    plan_status:  PlanStatus    = PlanStatus.DRAFT
    runtime_manifest_id: str   = ""

    def freeze(self) -> None:
        """Compute and lock the plan hash (A2)."""
        canonical = json.dumps(
            [{"id": t.id, "goal": t.goal, "reads": sorted(t.reads),
              "writes": sorted(t.writes), "tools": sorted(t.tools)}
             for t in self.tasks], sort_keys=True)
        self.plan_hash = hashlib.sha256(canonical.encode()).hexdigest()

    def transition(self, target: PlanStatus) -> None:
        if not self.plan_status.can_transition_to(target):
            raise AxiomViolation(
                f"Invalid plan status transition: {self.plan_status.value} -> {target.value}")
        self.plan_status = target


@_dc.dataclass
class ExecResult:
    task_id:         str
    artifacts:       list[str]
    token_usage:     int
    tool_invocations: list[dict]
    state:           TaskState
    notes:           str = ""

    def to_dict(self) -> dict:
        return _dc.asdict(self)


@_dc.dataclass
class VerificationOutcome:
    task_id:     str
    rubric_id:   str
    score:       float
    passed:      bool
    verdicts:    list[dict]
    notes:       str = ""

    def to_dict(self) -> dict:
        return _dc.asdict(self)


@_dc.dataclass
class GuidanceBlock:
    rules:      list[dict]
    risk:       RiskLevel
    checkpoint_every_pct: float = 1.0

    def to_dict(self) -> dict:
        return _dc.asdict(self)


@_dc.dataclass
class ContextView:
    """Quarantined view of data readable by a specific task (Axiom A3)."""
    task_id:    str
    allowed_reads: list[str]
    _data:      dict = _dc.field(default_factory=dict, repr=False)

    def read(self, key: str) -> Any:
        from essence.apde_types import ContextScopeError
        if key not in self.allowed_reads:
            raise ContextScopeError(
                f"Task {self.task_id} attempted out-of-scope read of '{key}'"
                f" (allowed: {self.allowed_reads})")
        return self._data.get(key)

    def load(self, data: dict) -> None:
        """Load data — only keys in allowed_reads are stored."""
        self._data = {k: v for k, v in data.items() if k in self.allowed_reads}


@_dc.dataclass
class ToolRecord:
    tool_name:               str
    capabilities:            list[str]
    requires_guardrails:     list[str]
    cost_class:              str
    max_invocations_per_task: int
    research_only:           bool = False

    def to_dict(self) -> dict:
        return _dc.asdict(self)


@_dc.dataclass
class TokenGrant:
    call_class:  CallClass
    max_tokens:  int
    task_id:     str
    granted_at:  float = _dc.field(default_factory=time.time)


@_dc.dataclass
class PlanDeltaRow:
    plan_id:    str
    seq:        int
    delta_type: str
    payload:    dict
    ts:         float = _dc.field(default_factory=time.time)

    def to_dict(self) -> dict:
        return _dc.asdict(self)
