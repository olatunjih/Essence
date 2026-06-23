"""CriticGate +  ComplexityRouter.
v29.0: +7 Analytics Engine analytical failure categories; see prism/analytical_core.py """
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.core.registry import REGISTRY  # noqa: F401  [real source bug: used below without import]

# CRITIC GATE  (CriticGate — plan-execute-critique validation)
# ══════════════════════════════════════════════════════════════════════════════
# Full implementation of the TaskPipeline 4-stage pipeline:
#   1. Trajectory normalization (tool call + result records)
#   2. Constraint synthesis from TOOLS.md domain policies
#   3. Guarded step evaluation (check only when guard applies)
#   4. LLM judge → 9-category failure taxonomy + critical failure step
#
# TaskPipeline improved failure localization +23.6% and root-cause +22.9%
# over naive prompting baselines (MSR paper, 2026-01-20).
#
# 9-category failure taxonomy (grounded-theory derived):
FAILURE_CATEGORIES = [
    "PlanAdherenceFailure",      # agent ignored its own planned steps
    "InventionOfInformation",    # hallucinated facts / tool results
    "ToolMisuse",                # wrong tool or wrong arguments
    "GoalDrift",                 # lost alignment with original objective
    "ContextLoss",               # forgot earlier facts over long horizon
    "InvalidState",              # action on invalid / stale state
    "PrematureTermination",      # stopped before task completion
    "PermissionViolation",       # tried blocked action (our sandbox caught it)
    "FormatError",               # output doesn't match expected schema
]

_CRITIC_GATE_SYS = (
    "You are a CriticGate validator. Given a step action and its result, "
    "evaluate whether the step completed correctly against the tool constraints. "
    "Respond ONLY with JSON (no markdown, no prose): "
    '{"pass": true/false, '
    '"category": "<one of: ' + '|'.join(FAILURE_CATEGORIES) + '> or null", '
    '"evidence": "<brief quote from result that shows the issue or null>", '
    '"fix_hint": "<concrete corrective action or null>"}'
)


class CriticResult(BaseModel):
    """Pydantic v2 model for CriticGate critic output.

    `from_json()` is synchronous, but instances are also awaitable (return
    themselves immediately) so call sites written as
    `cr = await CriticResult.from_json(...)` keep working alongside the
    more common `cr = CriticResult.from_json(...)`.
    """
    model_config = ConfigDict(frozen=True)

    passed:   bool
    category: str | None = None  # failure category or None
    evidence: str | None = None
    fix_hint: str | None = None

    def __await__(self):
        async def _identity():
            return self
        return _identity().__await__()

    @classmethod
    def ok(cls) -> 'CriticResult':
        return cls(passed=True, category=None, evidence=None, fix_hint=None)

    @classmethod
    def safe_block(cls, reason: str) -> 'CriticResult':
        """Return a blocking result used when parse fails after repair."""
        return cls(
            passed=False,
            category='FormatError',
            evidence=f'LLM output could not be parsed as valid CriticResult: {reason}',
            fix_hint='Re-run the step with a clearer prompt.',
        )

    @classmethod
    def _try_parse(cls, raw: str) -> "CriticResult | None":
        clean = re.sub(r"```[a-zA-Z]*", "", raw).strip()
        # Strip leading/trailing prose outside the JSON object
        m = re.search(r'\{(?:[^{}]|\{[^{}]*\})*"pass"(?:[^{}]|\{[^{}]*\})*\}', clean, re.S)
        if m:
            clean = m.group(0)
        try:
            d = json.loads(clean)
            # Type validation: "pass" must be bool-able, category must be str or None
            passed = bool(d.get("pass", True))
            category = d.get("category")
            if category is not None and not isinstance(category, str):
                return None
            return cls(
                passed=passed,
                category=category if category in FAILURE_CATEGORIES else None,
                evidence=str(d['evidence']) if d.get('evidence') else None,
                fix_hint=str(d['fix_hint']) if d.get('fix_hint') else None,
            )
        except Exception:
            return None

    @classmethod
    def from_json(cls, raw: str,
                  repair_fn: "Callable[[str, str], Any] | None" = None
                  ) -> "CriticResult":
        """
        Parse critic JSON with one repair_json retry on failure.
        repair_fn(raw, schema) -> re-prompted raw from LLM (sync, or async --
        an async repair_fn is resolved via asyncio.run() when there is no
        event loop already running; if one *is* running -- i.e. called from
        an `async def` -- use `await CriticResult.afrom_json(...)` instead).
        On second failure: safe_block (never silently pass a bad verdict).
        """
        result = cls._try_parse(raw)
        if result is not None:
            return result
        # One repair attempt
        if repair_fn is not None:
            schema = ('{"pass": true/false, "category": "' +
                      '|'.join(FAILURE_CATEGORIES) + ' or null", '
                      '"evidence": "string or null", "fix_hint": "string or null"}')
            try:
                repaired = repair_fn(raw, schema)
                if asyncio.iscoroutine(repaired):
                    repaired = asyncio.run(repaired)
                result2  = cls._try_parse(repaired)
                if result2 is not None:
                    return result2
            except Exception:
                pass
        return cls.safe_block(raw[:120])

    @classmethod
    async def afrom_json(cls, raw: str,
                  repair_fn: "Callable[[str, str], Any] | None" = None
                  ) -> "CriticResult":
        """Async counterpart of from_json, for use inside `async def`
        callers (e.g. Agent's ReAct loop) whose repair_fn is itself a
        coroutine function and must be awaited on the caller's event loop."""
        result = cls._try_parse(raw)
        if result is not None:
            return result
        if repair_fn is not None:
            schema = ('{"pass": true/false, "category": "' +
                      '|'.join(FAILURE_CATEGORIES) + ' or null", '
                      '"evidence": "string or null", "fix_hint": "string or null"}')
            try:
                repaired = repair_fn(raw, schema)
                if asyncio.iscoroutine(repaired):
                    repaired = await repaired
                result2  = cls._try_parse(repaired)
                if result2 is not None:
                    return result2
            except Exception:
                pass
        return cls.safe_block(raw[:120])


def _synthesise_constraints(tools_md: str) -> list[str]:
    """Extract constraint strings from TOOLS.md."""
    constraints = []
    for line in tools_md.splitlines():
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            constraints.append(line[2:].strip())
    return constraints


# ══════════════════════════════════════════════════════════════════════════════

# COMPLEXITY ROUTER
# ══════════════════════════════════════════════════════════════════════════════
# Routing heuristic:  TRIVIAL → T0 (0.6B)  |  SIMPLE → T0/T1 (1.7–4B)
#                     MODERATE → T1 (8B)   |  COMPLEX → T2 (14–32B)
#                     EXPERT → T3 (70B+)
# Saves 10–100x tokens on trivial steps while reserving large models for
# planning and multi-step reasoning. Integrates with SpecialistAgent routing.

class RequestComplexity(_enum.Enum):
    TRIVIAL  = 0   # single-fact lookup, echo, simple format
    SIMPLE   = 1   # one-shot answer, basic file op
    MODERATE = 2   # multi-turn, light code, short analysis
    COMPLEX  = 3   # multi-step plan, code review, data analysis
    EXPERT   = 4   # deep reasoning, long-horizon task, research


def _classify_complexity(text: str, tool: str = "none") -> RequestComplexity:
    """Heuristic complexity classifier — zero LLM calls, sub-millisecond."""
    t = text.lower()
    n = len(text.split())
    # Expert signals
    if any(k in t for k in ("research", "implement", "architect", "design system",
                             "refactor", "analyze dataset", "train", "finetune",
                             "multi-step", "long-term", "strategy")):
        return RequestComplexity.EXPERT
    # Complex signals
    if (n > 80 or any(k in t for k in ("plan", "steps to", "how do i", "compare",
                                        "explain in detail", "code that", "write a"))):
        return RequestComplexity.COMPLEX
    # Tool-based signals
    if tool in ("run_analysis", "train_model", "finetune", "vision_task"):
        return RequestComplexity.COMPLEX
    if tool in ("python_exec", "shell", "web_search"):
        return RequestComplexity.MODERATE
    # Moderate signals
    if n > 30 or any(k in t for k in ("summarize", "describe", "list", "find")):
        return RequestComplexity.MODERATE
    # Simple / trivial
    if n <= 8:
        return RequestComplexity.TRIVIAL
    return RequestComplexity.SIMPLE


def route_model_for_complexity(hw: "HardwareProfile",
                                complexity: RequestComplexity) -> str:
    """Return the best ollama_tag that handles this complexity within hw budget."""
    # Minimum tier needed per complexity level
    _tier_map = {
        RequestComplexity.TRIVIAL:  0,
        RequestComplexity.SIMPLE:   0,
        RequestComplexity.MODERATE: 1,
        RequestComplexity.COMPLEX:  2,
        RequestComplexity.EXPERT:   3,
    }
    min_tier = _tier_map[complexity]
    # Clamp to actual hw tier — can't use T3 on T1 hardware
    effective_tier = min(min_tier, hw.tier)
    budget = hw.effective_gb * 0.85
    candidates = sorted(
        [m for m in REGISTRY
         if m.min_tier <= effective_tier
         and m.vram_q4_gb <= budget
         and not m.requires_vlm],
        key=lambda m: (m.pinch, m.active_b), reverse=True,
    )
    if not candidates:
        return hw.model
    # For trivial/simple, pick the *smallest* that fits the tier
    if complexity in (RequestComplexity.TRIVIAL, RequestComplexity.SIMPLE):
        tier_candidates = [m for m in candidates if m.min_tier == 0]
        if tier_candidates:
            return sorted(tier_candidates, key=lambda m: m.vram_q4_gb)[0].ollama_tag
    return candidates[0].ollama_tag



# ══════════════════════════════════════════════════════════════════════════════

# ──  Analytics Engine analytical failure category extension ───────────────────────────
# Extend the v28.1 FAILURE_CATEGORIES list with the 7 new Analytics Engine categories
# and expose AnalyticalCriticGate as the preferred entry point for v29+ code.
try:
    from essence.analytics.analysis_critic import (   # noqa: F401
        AnalyticalCriticGate,
        AnalyticalCriticResult,
        GenesisFeedbackRecord,
        ALL_FAILURE_CATEGORIES,
    )
    # Extend the module-level list so CriticResult.from_json() accepts the
    # new categories without modification.
    _existing = set(FAILURE_CATEGORIES)
    for _cat in ALL_FAILURE_CATEGORIES:
        if _cat not in _existing:
            FAILURE_CATEGORIES.append(_cat)
            _existing.add(_cat)
except ImportError:
    pass  # prism not yet available; v28.1 behaviour preserved
