"""DatasetFingerprint, EntityProfile, DomainLens, Finding, AnalyticalStateBus stub."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# CORE Analytics Engine DATA MODELS
# ══════════════════════════════════════════════════════════════════════════════

class DatasetFingerprint(BaseModel):
    """Statistical signature of a dataset for archetype clustering."""
    model_config = ConfigDict(frozen=True)

    n_rows_bucket: str
    n_cols_bucket: str
    type_distribution: dict[str, float]
    sparsity: float
    dominant_distribution: str
    entropy_profile: dict[str, float]
    temporal: bool
    cardinality_profile: dict[str, float]
    event_density: float
    sub_event_richness: int
    entity_count: int
    resolution_levels: list[int]

class EntityProfile(BaseModel):
    """Analytical dossier for a discovered entity."""
    model_config = ConfigDict(frozen=True)

    id: str
    observation_count: int
    time_span: float
    completeness: float
    feature_means: dict[str, float]
    feature_stds: dict[str, float]
    event_rates: dict[str, float]
    consistency: float
    distinctiveness: float
    archetype_id: str | None = None
    strength: float | None = None

class DomainLens(BaseModel):
    """Injectable domain context for mathematical analysis interpretation."""
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    feature_classifiers: list[dict] = Field(default_factory=list)
    sub_event_taxonomy: list[dict] = Field(default_factory=list)
    entity_interaction_model: dict = Field(default_factory=dict)
    relationship_interpreters: list[dict] = Field(default_factory=list)
    anomaly_interpreters: list[dict] = Field(default_factory=list)
    threshold_overrides: dict = Field(default_factory=dict)
    narrative_templates: dict = Field(default_factory=dict)
    compliance_hooks: list[dict] = Field(default_factory=list)

class Finding(BaseModel):
    """A typed, scored, evidence-chained analytical discovery."""
    model_config = ConfigDict(frozen=False)

    id: str = Field(default_factory=lambda: str(hashlib.md5(secrets.token_bytes(16)).hexdigest()))
    category: str
    sub_category: str
    title: str
    description: str

    # SCORES (0.0-1.0)
    confidence: float
    calibrated_confidence: float
    impact: float
    actionability: float
    novelty: float
    robustness: float
    fragility: float

    # EVIDENCE
    source_layer: str
    statistical_test: str | None = None
    test_statistic: float | None = None
    p_value: float | None = None
    sample_size: int | None = None
    effect_size: float | None = None
    assumptions_met: bool = True
    assumptions_violated: list[str] = Field(default_factory=list)
    bootstrap_confidence: float | None = None
    permutation_p: float | None = None
    cross_resolution_confirmed: bool = False

    # EPISTEMICS
    falsification_criteria: str
    uncertainty_type: str  # aleatoric | epistemic | mixed
    data_needed_to_resolve: str | None = None
    known_confounders: list[str] = Field(default_factory=list)
    simpson_risk: bool = False
    causal_level: str = "associational" # associational | temporal | interventional
    resolution_dependency: str | None = None

    # LIFECYCLE
    status: str = "ACTIVE" # ACTIVE | TENTATIVE | SUPERSEDED | RETRACTED
    wave_discovered: int = 1
    wave_last_refined: int = 1
    superseded_by: str | None = None
    retraction_reason: str | None = None
    history: list[tuple[float, str, str]] = Field(default_factory=list)

    # CONTEXTUAL DATA
    edge: dict = Field(default_factory=dict)
    entity: dict = Field(default_factory=dict)
    sub_event: dict = Field(default_factory=dict)
    domain: dict = Field(default_factory=dict)
    connections: dict = Field(default_factory=dict)
    visualization: dict = Field(default_factory=dict)

class AnalyticalStateBus(BaseModel):
    """The Analytical Spine — real-time bus of analytical state."""
    model_config = ConfigDict(frozen=False)

    active_lens: DomainLens | None = None
    active_archetype: str = "unknown"
    active_fingerprint: DatasetFingerprint | None = None
    active_entities: list[EntityProfile] = Field(default_factory=list)
    active_findings: list[Finding] = Field(default_factory=list)
    active_edges: list[dict] = Field(default_factory=list)
    trust_score: float = 1.0
    contradiction_log: list[dict] = Field(default_factory=list)
    drift_alerts: list[dict] = Field(default_factory=list)

# ══════════════════════════════════════════════════════════════════════════════
