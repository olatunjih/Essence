"""Intent layer: NormalizedInput → TaskSpec.
v29.0: AnalyticalIntentLayer wraps this; see prism/analytical_core.py """
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

from essence.analytics.spine import AnalyticalIntentLayer, get_intent_layer  # noqa: F401
from essence.agents.critic import RequestComplexity, _classify_complexity  # noqa: F401  [real source bug]
# INTENT LAYER
# ══════════════════════════════════════════════════════════════════════════════

@_dc.dataclass
class NormalizedInput:
    """Unified multi-modal input format (Text, Files, Images, Events)."""
    text:     str
    files:    list[str] = _dc.field(default_factory=list)
    images:   list[str] = _dc.field(default_factory=list)
    metadata: dict      = _dc.field(default_factory=dict)
    event_id: str       = ""

class InputNormalizer:
    """Unifies multi-modal inputs into a common NormalizedInput format."""
    def normalize(self, raw: Any) -> NormalizedInput:
        if isinstance(raw, str):
            return NormalizedInput(text=raw)
        if isinstance(raw, dict):
            return NormalizedInput(
                text=raw.get("text", ""),
                files=raw.get("files", []),
                images=raw.get("images", []),
                metadata=raw.get("metadata", {}),
                event_id=raw.get("event_id", "")
            )
        return NormalizedInput(text=str(raw))

class LanguageUnderstanding:
    """Deep NLU: Entity extraction, coreference resolution, and intent analysis."""
    def __init__(self, provider: Any = None, model: str = ""):
        self.provider = provider
        self.model = model

    def extract_entities(self, text: str) -> list[dict]:
        """Extract entities (people, places, orgs, dates) from text."""
        # Baseline implementation uses regex; can be upgraded to LLM call
        entities = []
        # Dates
        for m in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b", text):
            entities.append({"text": m.group(0), "type": "DATE"})
        # Simple Email
        for m in re.finditer(r"[\w.]+@[\w.]+", text):
            entities.append({"text": m.group(0), "type": "EMAIL"})
        return entities

    def resolve_coreferences(self, text: str, context: str = "") -> str:
        """Resolve simple pronouns (he/she/it/they/his/her/its/their) by
        substituting the most-recently-mentioned noun phrase extracted from
        *context*.  This is a lightweight heuristic resolver — not deep NLU —
        sufficient to eliminate the most common single-sentence ambiguities.
        For production accuracy, replace with a neural coref model."""
        if not context or not text:
            return text

        import re as _re

        # Extract candidate antecedents: capitalised words / quoted phrases
        antecedents: list[str] = _re.findall(
            r'"([^"]+)"|\'([^\']+)\'|([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
            context,
        )
        # Flatten and keep non-empty, last-mentioned first
        candidates: list[str] = []
        for groups in reversed(antecedents):
            cand = next((g for g in groups if g), "")
            if cand and cand not in candidates:
                candidates.append(cand)

        if not candidates:
            return text

        # Map pronoun → best antecedent (first candidate as default)
        top = candidates[0]
        pronoun_map = {
            r"\bhe\b": top, r"\bshe\b": top, r"\bthey\b": top,
            r"\bhim\b": top, r"\bher\b": top, r"\bthem\b": top,
            r"\bhis\b": top + "'s", r"\bits\b": top + "'s",
            r"\btheir\b": top + "'s",
        }
        result = text
        for pattern, replacement in pronoun_map.items():
            result = _re.sub(pattern, replacement, result, flags=_re.IGNORECASE)
        return result

class ContextInjector:
    """Formal component to inject User Profile, Session State, and Environment context."""
    def __init__(self, workspace: Path | None = None):
        self.workspace = workspace

    def inject(self, task_spec: "TaskSpec", session_id: str = "", user_id: str = "") -> "TaskSpec":
        """Injects contextual data into the TaskSpec."""
        # Inject session ID if missing
        if not task_spec.session_id:
            task_spec.session_id = session_id

        # Inject environment info
        task_spec.context["platform"] = sys.platform
        task_spec.context["python_version"] = sys.version.split()[0]
        task_spec.context["timestamp"] = time.time()

        # In a real system, this would load from a UserProfileStore
        if user_id:
            task_spec.user_id = user_id
            task_spec.context["user_profile"] = {"id": user_id, "role": "admin"}

        return task_spec

@_dc.dataclass
class TaskConstraint:
    "Extracted constraint from user input."
    kind:  str
    value: str
    hard:  bool = True

@_dc.dataclass
class TaskSpec:
    "Structured intent specification — output of the Intent Layer."
    goal:        str
    subtasks:    list[dict]           = _dc.field(default_factory=list)
    constraints: list[TaskConstraint] = _dc.field(default_factory=list)
    priority:    str                  = "medium"
    complexity:  "RequestComplexity"  = RequestComplexity.MODERATE
    context:     dict                 = _dc.field(default_factory=dict)
    user_id:     str                  = ""
    session_id:  str                  = ""

    # v29 Analytics Engine fields
    analytical_mode: str | None = None # EXPLORE | CORRELATE | PREDICT | etc.
    prism_config: dict = _dc.field(default_factory=dict)

_CONSTRAINT_PATTERNS: list[tuple[str, str]] = [
    (r"within\s+(\d+)\s*(minutes?|mins?|hours?|hrs?|seconds?|secs?|days?)", "time"),
    (r"budget\s*(?:of|:)?\s*\$?(\d+)", "budget"),
    (r"(?:output|return|format)\s+(?:as|in)\s+(json|csv|markdown|html|pdf)", "format"),
    (r"(?:no|avoid|don.?t|without)\s+(network|internet|api|cloud)", "scope"),
    (r"(?:low|minimal|zero)\s+risk", "risk"),
    (r"(?:use|only|prefer)\s+(shell|python|web_search|read_file)", "tool"),
]

def extract_constraints(text: str) -> list[TaskConstraint]:
    "Extract typed constraints from natural language user input."
    constraints: list[TaskConstraint] = []
    for pattern, kind in _CONSTRAINT_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            constraints.append(TaskConstraint(kind=kind, value=m.group(0).strip()))
    return constraints

def _classify_priority(text: str) -> str:
    "Heuristic priority classifier from user input."
    t = text.lower()
    if any(k in t for k in ("urgent", "asap", "immediately", "critical")): return "critical"
    if any(k in t for k in ("important", "high priority", "soon")): return "high"
    if any(k in t for k in ("when you can", "low priority", "no rush")): return "low"
    return "medium"

def build_task_spec(user_input: Any, session_id: str = "",
                    user_id: str = "", context: dict | None = None,
                    provider: Any = None, model: str = "") -> TaskSpec:
    """Build a structured TaskSpec from raw multi-modal user input."""
    # 1. Normalization
    normalizer = InputNormalizer()
    norm = normalizer.normalize(user_input)

    # 2. Language Understanding (Deep NLU)
    nlu = LanguageUnderstanding(provider, model)
    text = nlu.resolve_coreferences(norm.text)
    entities = nlu.extract_entities(text)

    # 3. Assemble initial TaskSpec
    spec = TaskSpec(
        goal=text.strip(),
        constraints=extract_constraints(text),
        priority=_classify_priority(text),
        complexity=_classify_complexity(text),
        context=context or {},
        user_id=user_id,
        session_id=session_id
    )
    spec.context["entities"] = entities
    spec.context["metadata"] = norm.metadata
    if norm.files: spec.context["files"] = norm.files
    if norm.images: spec.context["images"] = norm.images

    # 4. Context Injection
    injector = ContextInjector()
    spec = injector.inject(spec, session_id=session_id, user_id=user_id)

    # 5. v29 Analytical Intent Classification — delegates to the full
    # AnalyticalIntentLayer (prism/analytical_core.py ) rather than
    # duplicating a simpler 4-mode keyword check here. This was previously
    # dead code: AnalyticalIntentLayer was imported above but never called,
    # so Analytics Engine features it provides (8-mode classification incl.
    # DIAGNOSE/MONITOR/COMPOSE/PROFILE, domain_hint from the active Domain Lens
    # lens, contextual signals from prior active_findings, feature/entity
    # extraction, and the active-spine context injected onto
    # spec.context["_prism_spine"]) never activated.
    try:
        spec = get_intent_layer(provider, model).enhance_task_spec(spec, text, context)
    except Exception as e:
        log.debug("analytical_intent_layer_error", extra={"error": str(e)[:160]})

    return spec


# ══════════════════════════════════════════════════════════════════════════════
