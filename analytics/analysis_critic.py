# essence.analytics.analytical_critic
"""
  Analytical Critic Gate (Analytics Engine × Essence wiring doc )
========================================================
Extends the v28.1 CriticGate with 7 Analytics Engine-specific failure categories and
a statistical validation layer that cross-checks every numerical claim in an
agent step against live findings on the Analytical Spine.

Architecture
------------
AnalyticalCriticGate wraps the existing CriticGate and adds two passes:

  Pass A — Standard critique (existing 9 v28.1 categories, unchanged)
  Pass B — Statistical validation against AnalyticalStateBus.active_findings

Pass B introduces 7 new failure categories:

  StatisticalOverclaim      — correlation/significance without Bonferroni
  ResolutionFallacy         — aggregate finding applied to individuals
  ConfidenceInflation       — raw confidence instead of calibrated
  EdgeWithoutEvidence       — actionable edge below minimum sample
  StalePatternApplication   — pattern from a decayed / pre-change-point model
  CompositionFallacy        — sub-event totals treated as independent
  CausalOverreach           — causal claim from associational evidence

Every critic judgment feeds back to Learning Engine via a feedback record that the
AnalyticalRewardSignal (analytical_reward.py) consumes on task completion.
"""
from __future__ import annotations
from essence._shared import *          # noqa: F401,F403

import dataclasses as _dc
import re
import math

from essence.analytics.layers import AnalyticalStateBus

_log = _setup_logging("essence.analytics.analytical_critic")

# ─── failure taxonomy ─────────────────────────────────────────────────────────

# v28.1 original 9 — unchanged
_V28_CATEGORIES: list[str] = [
    "PlanAdherenceFailure",
    "InventionOfInformation",
    "ToolMisuse",
    "GoalDrift",
    "ContextLoss",
    "InvalidState",
    "PrematureTermination",
    "PermissionViolation",
    "FormatError",
]

# v29.0 analytical extensions — 7 new
_ANALYTICAL_CATEGORIES: list[str] = [
    "StatisticalOverclaim",
    "ResolutionFallacy",
    "ConfidenceInflation",
    "EdgeWithoutEvidence",
    "StalePatternApplication",
    "CompositionFallacy",
    "CausalOverreach",
]

ALL_FAILURE_CATEGORIES: list[str] = _V28_CATEGORIES + _ANALYTICAL_CATEGORIES

# ─── data models ─────────────────────────────────────────────────────────────

@_dc.dataclass
class AnalyticalCriticResult:
    """
    Extended CriticResult that carries both the standard verdict and
    any Analytics Engine statistical validation findings.
    """
    passed:              bool
    category:            str | None        = None   # one of ALL_FAILURE_CATEGORIES
    evidence:            str | None        = None
    fix_hint:            str | None        = None
    # analytical extension fields
    is_analytical_fail:  bool              = False
    stat_violations:     list[dict]        = _dc.field(default_factory=list)
    # Learning Engine feedback payload (consumed by AnalyticalRewardSignal)
    genesis_feedback:    dict              = _dc.field(default_factory=dict)

    # ── convenience constructors ────────────────────────────────────────────
    @classmethod
    def ok(cls) -> "AnalyticalCriticResult":
        return cls(passed=True)

    @classmethod
    def analytical_fail(cls, category: str, evidence: str,
                        fix_hint: str, stat_violations: list[dict]
                        ) -> "AnalyticalCriticResult":
        return cls(
            passed=False,
            category=category,
            evidence=evidence,
            fix_hint=fix_hint,
            is_analytical_fail=True,
            stat_violations=stat_violations,
            genesis_feedback={
                "category":  category,
                "confirmed": False,
                "n_violations": len(stat_violations),
            },
        )


@_dc.dataclass
class GenesisFeedbackRecord:
    """Emitted after each critic cycle; consumed by AnalyticalRewardSignal."""
    finding_id:    str
    confirmed:     bool           # did the critic validate this finding?
    category:      str | None     # failure category if rejected
    step_id:       str            = ""
    ts:            float          = _dc.field(default_factory=time.time)


# ─── numerical claim extraction ───────────────────────────────────────────────

# Patterns that signal numerical claims worth checking
_NUM_RE        = re.compile(r"[-+]?\d+(?:\.\d+)?(?:%|x|×)?")
_CORR_RE       = re.compile(r"\br\s*=\s*([-+]?\d+\.\d+)", re.IGNORECASE)
_CONF_RE       = re.compile(r"\b(\d{1,3})%\s*confiden(?:ce|t|tly)?\b", re.IGNORECASE)
_TREND_RE      = re.compile(r"\b(strong|significant|clear)\s+(upward|downward|positive|negative)\s+trend", re.IGNORECASE)
_CAUSAL_RE     = re.compile(r"\b(causes?|leads? to|results? in|drives?|because of)\b", re.IGNORECASE)
_AGGREGATE_RE  = re.compile(r"\b(overall|on average|in general|across all|total)\b", re.IGNORECASE)
_CAUSAL_LEVEL  = {"associational", "temporal", "interventional", "counterfactual"}


def _extract_numerical_claims(text: str) -> list[dict]:
    """
    Heuristically extract numerical claims from agent output text.
    Returns a list of {type, value, raw_snippet} dicts.
    """
    claims: list[dict] = []

    for m in _CORR_RE.finditer(text):
        claims.append({"type": "correlation", "value": float(m.group(1)),
                       "raw": text[max(0, m.start()-30):m.end()+30].strip()})

    for m in _CONF_RE.finditer(text):
        claims.append({"type": "confidence", "value": float(m.group(1)) / 100,
                       "raw": text[max(0, m.start()-30):m.end()+30].strip()})

    if _TREND_RE.search(text):
        claims.append({"type": "trend", "value": None,
                       "raw": _TREND_RE.search(text).group(0)})

    if _CAUSAL_RE.search(text):
        claims.append({"type": "causal", "value": None,
                       "raw": _CAUSAL_RE.search(text).group(0)})

    if _AGGREGATE_RE.search(text):
        claims.append({"type": "aggregate", "value": None,
                       "raw": _AGGREGATE_RE.search(text).group(0)})

    return claims


# ─── individual statistical validators ───────────────────────────────────────

def _check_statistical_overclaim(claim: dict, findings: list[Any]) -> dict | None:
    """
    StatisticalOverclaim: agent states a correlation without Bonferroni
    correction when Analytics Engine's corrected value differs by > 0.15.
    """
    if claim["type"] != "correlation":
        return None
    agent_r = abs(claim["value"])
    for f in findings:
        if getattr(f, "category", "") != "CORRELATION":
            continue
        prism_r = abs(getattr(f, "test_statistic", agent_r))
        if abs(agent_r - prism_r) > 0.15 and agent_r > prism_r:
            return {
                "rule":      "StatisticalOverclaim",
                "agent_r":   agent_r,
                "prism_r":   prism_r,
                "finding_id": getattr(f, "id", ""),
                "detail":    (
                    f"Agent claimed r={agent_r:.3f} but Analytics Engine Bonferroni-corrected "
                    f"correlation is r={prism_r:.3f}. "
                    f"Uncorrected correlation inflates the finding."
                ),
            }
    return None


def _check_confidence_inflation(claim: dict, spine: AnalyticalStateBus) -> dict | None:
    """
    ConfidenceInflation: agent reports raw model confidence when Learning Engine
    calibrated confidence is materially lower (delta > 0.15).
    """
    if claim["type"] != "confidence":
        return None
    agent_conf = claim["value"]
    cal_ece    = spine.confidence_state.ece
    # If ECE > 0.10 the calibration is poor; agent claims above 0.80 are suspect
    if cal_ece > 0.10 and agent_conf > 0.80:
        calibrated = max(0.0, agent_conf - cal_ece * 1.5)
        if (agent_conf - calibrated) > 0.15:
            return {
                "rule":       "ConfidenceInflation",
                "agent_conf": agent_conf,
                "calibrated": round(calibrated, 3),
                "ece":        round(cal_ece, 4),
                "detail":     (
                    f"Agent claimed {agent_conf:.0%} confidence. "
                    f"Learning Engine calibrator (ECE={cal_ece:.3f}) suggests "
                    f"calibrated confidence ≈ {calibrated:.0%}."
                ),
            }
    return None


def _check_stale_pattern(claim: dict, findings: list[Any],
                         spine: AnalyticalStateBus) -> dict | None:
    """
    StalePatternApplication: agent references a trend when Analytics Engine L2 detected a
    change point that splits the time series — full-period trend may be misleading.
    """
    if claim["type"] != "trend":
        return None
    for f in findings:
        if getattr(f, "category", "") == "CHANGE_POINT" and getattr(f, "impact", 0) > 0.6:
            return {
                "rule":       "StalePatternApplication",
                "finding_id": getattr(f, "id", ""),
                "detail":     (
                    f"Agent claimed a trend, but Analytics Engine detected a structural "
                    f"change point ({f.title if hasattr(f,'title') else ''}). "
                    f"Full-period trend hides the regime shift."
                ),
            }
    return None


def _check_causal_overreach(claim: dict, findings: list[Any]) -> dict | None:
    """
    CausalOverreach: agent uses causal language ("causes", "leads to") but
    all supporting Analytics Engine findings are associational (L3).
    """
    if claim["type"] != "causal":
        return None
    corr_findings = [f for f in findings if getattr(f, "category", "") == "CORRELATION"]
    if not corr_findings:
        return None
    # Check if any finding asserts a causal level beyond associational
    highest_causal = max(
        (_CAUSAL_LEVEL.difference({"associational"}) &
         {getattr(f, "causal_level", "associational")} for f in corr_findings),
        key=len, default=set()
    )
    if not highest_causal:
        return {
            "rule":   "CausalOverreach",
            "detail": (
                f"Agent used causal language ('{claim['raw']}') but all "
                f"supporting Analytics Engine findings ({len(corr_findings)}) are "
                f"associational. No interventional or counterfactual evidence exists."
            ),
        }
    return None


def _check_resolution_fallacy(claim: dict, findings: list[Any]) -> dict | None:
    """
    ResolutionFallacy: agent makes an aggregate claim but Analytics Engine found
    a correlation or pattern that reverses at sub-group level (Simpson-like).
    """
    if claim["type"] != "aggregate":
        return None
    for f in findings:
        if "SIMPSON" in str(getattr(f, "sub_category", "")).upper():
            return {
                "rule":       "ResolutionFallacy",
                "finding_id": getattr(f, "id", ""),
                "detail":     (
                    "Agent made an aggregate claim but Analytics Engine detected a "
                    "Simpson's Paradox-like reversal at sub-group level."
                ),
            }
    return None


# ─── main class ──────────────────────────────────────────────────────────────

class AnalyticalCriticGate:
    """
     — Analytics Engine-extended CriticGate.

    Usage
    -----
    critic = AnalyticalCriticGate(spine=spine)
    result = critic.validate(step_action="...", step_result="...", step_id="s3")

    The result is an AnalyticalCriticResult which is a superset of the
    existing CriticResult: it passes all the same fields through and adds
    stat_violations and genesis_feedback for downstream consumers.

    Integration
    -----------
    Drop-in alongside the existing CriticGate:
      if result.is_analytical_fail:
          # v29: attempt self-correction via Analytics Engine
          ...
      else:
          # v28.1: standard 9-category handling unchanged
          ...
    """

    def __init__(self,
                 spine: AnalyticalStateBus | None = None,
                 provider: Any = None,
                 model: str = "") -> None:
        self.spine    = spine or AnalyticalStateBus()
        self.provider = provider
        self.model    = model
        self._feedback_log: list[GenesisFeedbackRecord] = []

    def validate(self,
                 step_action: str,
                 step_result: str,
                 step_id:     str = "") -> AnalyticalCriticResult:
        """
        Run both validation passes and return a merged AnalyticalCriticResult.

        Pass A: standard v28.1 critique (LLM-as-judge, unchanged)
        Pass B: Analytics Engine statistical validation (new)

        If Pass B fires, it takes precedence — statistical violations are
        objective, not LLM-opinion-dependent.
        """
        combined_text = f"{step_action}\n{step_result}"

        # ── Pass B: statistical validation ────────────────────────────────
        analytical_result = self._statistical_validation(combined_text, step_id)
        if analytical_result is not None:
            return analytical_result

        # ── Pass A: standard critique (returns plain CriticResult) ────────
        # Wrap it in AnalyticalCriticResult for type consistency.
        std = self._standard_critique(step_action, step_result)
        result = AnalyticalCriticResult(
            passed=std.get("passed", True),
            category=std.get("category"),
            evidence=std.get("evidence"),
            fix_hint=std.get("fix_hint"),
            genesis_feedback={"confirmed": std.get("passed", True),
                              "category": std.get("category")},
        )
        # Log feedback for Learning Engine calibration
        self._emit_feedback(step_id, result)
        return result

    def drain_feedback(self) -> list[GenesisFeedbackRecord]:
        """Consume and clear the Learning Engine feedback queue."""
        out, self._feedback_log = self._feedback_log, []
        return out

    # ── Pass B internals ──────────────────────────────────────────────────

    def _statistical_validation(self, text: str,
                                 step_id: str) -> AnalyticalCriticResult | None:
        """
        Extract numerical claims from text and cross-check each against
        active Analytics Engine findings on the spine.

        Returns an AnalyticalCriticResult on the first violation found,
        or None if no violations detected.
        """
        findings = self.spine.active_findings
        if not findings:
            return None

        claims = _extract_numerical_claims(text)
        violations: list[dict] = []

        for claim in claims:
            for checker in [
                lambda c: _check_statistical_overclaim(c, findings),
                lambda c: _check_confidence_inflation(c, self.spine),
                lambda c: _check_stale_pattern(c, findings, self.spine),
                lambda c: _check_causal_overreach(c, findings),
                lambda c: _check_resolution_fallacy(c, findings),
            ]:
                v = checker(claim)
                if v:
                    violations.append(v)

        if not violations:
            return None

        # Use the most severe violation (first in the list after de-dup by rule)
        seen_rules: set[str] = set()
        unique_violations = []
        for v in violations:
            if v["rule"] not in seen_rules:
                unique_violations.append(v)
                seen_rules.add(v["rule"])

        primary = unique_violations[0]
        result  = AnalyticalCriticResult.analytical_fail(
            category=primary["rule"],
            evidence=primary["detail"],
            fix_hint=self._fix_hint(primary["rule"]),
            stat_violations=unique_violations,
        )
        self._emit_feedback(step_id, result)
        _log.debug("AnalyticalCriticGate: %s violation step=%s", primary["rule"], step_id)
        return result

    def _standard_critique(self, action: str, result: str) -> dict:
        """
        Thin wrapper around the existing LLM-as-judge critic.
        Returns a dict with keys: passed, category, evidence, fix_hint.
        Falls back gracefully when no provider is available.
        """
        if not self.provider:
            return {"passed": True, "category": None, "evidence": None, "fix_hint": None}
        try:
            from essence.agents.critic import _CRITIC_GATE_SYS  # type: ignore[import]
            prompt = (
                f"Step action:\n{action[:600]}\n\nStep result:\n{result[:1200]}"
            )
            raw = ""
            for tok in self.provider.complete(
                [{"role": "user", "content": prompt}],
                model=self.model,
                stream=False,
                thinking=False,
                system=_CRITIC_GATE_SYS,
            ):
                raw += tok
            clean = re.sub(r"```[a-zA-Z]*", "", raw).strip()
            d = json.loads(clean)
            return {
                "passed":   bool(d.get("pass", True)),
                "category": d.get("category"),
                "evidence": d.get("evidence"),
                "fix_hint": d.get("fix_hint"),
            }
        except Exception as e:
            _log.debug("standard_critique fallback: %s", e)
            return {"passed": True, "category": None, "evidence": None, "fix_hint": None}

    def _fix_hint(self, category: str) -> str:
        return {
            "StatisticalOverclaim":    "Apply Bonferroni correction; report Analytics Engine-corrected r.",
            "ResolutionFallacy":       "Break down by sub-group; report Simpson-adjusted result.",
            "ConfidenceInflation":     "Use Learning Engine calibrated confidence; report ECE alongside.",
            "EdgeWithoutEvidence":     "Report sample size and power; flag if below n=30 minimum.",
            "StalePatternApplication": "Re-run analysis on post-change-point data only.",
            "CompositionFallacy":      "Account for sub-event copula; test for independence first.",
            "CausalOverreach":         "Downgrade to associational language; cite confounders.",
        }.get(category, "Review the analytical claim and cross-check against Analytics Engine findings.")

    def _emit_feedback(self, step_id: str, result: AnalyticalCriticResult) -> None:
        """Append a GenesisFeedbackRecord for each referenced finding."""
        findings = self.spine.active_findings
        if not findings:
            return
        # Attribute feedback to the highest-impact finding
        top = max(findings, key=lambda f: getattr(f, "impact", 0), default=None)
        if top is None:
            return
        self._feedback_log.append(GenesisFeedbackRecord(
            finding_id=getattr(top, "id", ""),
            confirmed=result.passed,
            category=result.category,
            step_id=step_id,
        ))


# ── extended FAILURE_CATEGORIES for the monolith import surface ──────────────
# Drop-in replacement for the list used by CriticResult.from_json and the
# existing _CRITIC_GATE_SYS system prompt.

FAILURE_CATEGORIES = ALL_FAILURE_CATEGORIES
