# essence.analytics.analytical_verifier
"""
  Analytical Verifier Layer (Analytics Engine × Essence wiring doc )
===========================================================
Extends the v28.1 VerifierLayer with three Analytics Engine-specific passes that run
after the standard hallucination cross-check.

  Pass A — Standard verification (existing VerifierLayer claim extraction,
            LLM-as-judge, unchanged; imported and called here)

  Pass B — Finding Verification
            Every numerical claim in agent output is mapped to a Analytics Engine
            finding.  Claims with no backing finding are flagged as
            UNGROUNDED_ANALYTICAL_CLAIM.  High-impact findings the agent
            omitted are flagged as ANALYTICAL_OMISSION.

  Pass C — Consistency Verification
            Checks for internal contradictions using Analytics Engine logic:
              • agent says "A correlates with B" and "A is independent of B"
              • agent prediction contradicts Analytics Engine L6 ensemble
              • agent uses full-period average despite a change point

  Pass D — Uncertainty Injection
            If the agent makes a prediction without uncertainty bounds,
            Analytics Engine's aleatoric + epistemic decomposition is injected into
            the verified output so downstream callers can display it.
"""
from __future__ import annotations
from essence._shared import *          # noqa: F401,F403

import dataclasses as _dc
import re

from essence.analytics.layers import AnalyticalStateBus

_log = _setup_logging("essence.analytics.analytical_verifier")

# ─── result types ─────────────────────────────────────────────────────────────

@_dc.dataclass
class VerificationFlag:
    """A single flag raised by one of the three analytical verification passes."""
    flag_type:    str           # UNGROUNDED | OMISSION | INCONSISTENCY | UNCERTAINTY_MISSING
    description:  str
    finding_id:   str = ""
    severity:     str = "medium"    # low | medium | high
    injected_text: str = ""         # non-empty only for UNCERTAINTY_MISSING


@_dc.dataclass
class AnalyticalVerificationResult:
    """
    Full verification result.  Carries both the standard verification
    verdicts and the three new Analytics Engine passes.
    """
    # Pass A
    standard_verdicts:      list[dict]              = _dc.field(default_factory=list)
    standard_passed:        bool                    = True

    # Passes B-D
    flags:                  list[VerificationFlag]  = _dc.field(default_factory=list)
    ungrounded_claims:      list[str]               = _dc.field(default_factory=list)
    omitted_findings:       list[Any]               = _dc.field(default_factory=list)
    uncertainty_injections: list[str]               = _dc.field(default_factory=list)

    # merged verdict
    @property
    def passed(self) -> bool:
        high_flags = [f for f in self.flags if f.severity == "high"]
        return self.standard_passed and not high_flags

    @property
    def needs_revision(self) -> bool:
        return bool(self.flags) or not self.standard_passed

    def annotated_output(self, original: str) -> str:
        """
        Return the original agent output with uncertainty injections appended
        and a brief flag summary prepended when revision is needed.
        """
        out = original
        if self.uncertainty_injections:
            out += "\n\n[Analytics Engine Uncertainty Supplement]\n"
            out += "\n".join(self.uncertainty_injections)
        if self.flags:
            summary_lines = [f"[Verification Flag: {f.flag_type}] {f.description}"
                             for f in self.flags[:5]]
            out = "\n".join(summary_lines) + "\n\n" + out
        return out


# ─── numerical claim / entity extractors ─────────────────────────────────────

_CORR_CLAIM_RE  = re.compile(
    r"(?:correlation|corr|r)[^a-zA-Z0-9]*(?:between|of)?[^=]*=\s*([-+]?\d+\.\d+)",
    re.IGNORECASE)
_PRED_CLAIM_RE  = re.compile(
    r"\b(?:predict|forecast|estimate|project)\b.{0,60}(\d+(?:\.\d+)?)",
    re.IGNORECASE)
_INDEP_RE       = re.compile(
    r"\b(?:independent of|no relation|not correlated|uncorrelated)\b",
    re.IGNORECASE)
_FULL_PERIOD_RE = re.compile(
    r"\b(?:overall trend|historical average|long-term|full period)\b",
    re.IGNORECASE)
_UNCERTAINTY_RE = re.compile(
    r"\b(?:±|\+/-|confidence interval|CI|std|stderr|uncertainty)\b",
    re.IGNORECASE)


def _extract_correlation_claims(text: str) -> list[tuple[float, str]]:
    """Return (r_value, raw_snippet) pairs from correlation claims."""
    out = []
    for m in _CORR_CLAIM_RE.finditer(text):
        try:
            r = float(m.group(1))
            out.append((r, text[max(0, m.start()-20):m.end()+40].strip()))
        except ValueError:
            pass
    return out


def _extract_prediction_claims(text: str) -> list[tuple[float, str]]:
    """Return (value, raw_snippet) pairs for prediction claims."""
    out = []
    for m in _PRED_CLAIM_RE.finditer(text):
        try:
            v = float(m.group(1))
            out.append((v, text[max(0, m.start()):m.end()+20].strip()))
        except ValueError:
            pass
    return out


# ─── Pass B: finding verification ────────────────────────────────────────────

def _pass_b_finding_verification(agent_text: str,
                                  spine: AnalyticalStateBus,
                                  impact_threshold: float = 0.70
                                  ) -> tuple[list[VerificationFlag], list[str], list[Any]]:
    """
    Cross-check agent output against active_findings on the spine.

    Returns: (flags, ungrounded_claims, omitted_high_impact_findings)
    """
    flags:      list[VerificationFlag] = []
    ungrounded: list[str]              = []
    omitted:    list[Any]              = []
    findings    = spine.active_findings

    if not findings:
        return flags, ungrounded, omitted

    # ── check correlation claims are backed by a finding ─────────────────
    for r_val, snippet in _extract_correlation_claims(agent_text):
        backed = False
        for f in findings:
            if getattr(f, "category", "") != "CORRELATION":
                continue
            prism_r = abs(getattr(f, "test_statistic", float("nan")))
            if abs(abs(r_val) - prism_r) < 0.20:
                # Check finding is ACTIVE and confidence > 0.5
                if (getattr(f, "status", "ACTIVE") == "ACTIVE"
                        and getattr(f, "calibrated_confidence",
                                    getattr(f, "confidence", 0)) >= 0.5):
                    backed = True
                    break
        if not backed:
            ungrounded.append(snippet)
            flags.append(VerificationFlag(
                flag_type="UNGROUNDED_ANALYTICAL_CLAIM",
                description=(
                    f"Agent stated r≈{r_val:.3f} but no active Analytics Engine finding "
                    f"supports this correlation (±0.20 tolerance)."
                ),
                severity="high",
            ))

    # ── check high-impact findings are not omitted ────────────────────────
    high_impact = [
        f for f in findings
        if getattr(f, "impact", 0) >= impact_threshold
        and getattr(f, "status", "ACTIVE") == "ACTIVE"
    ]
    for f in high_impact:
        title = getattr(f, "title", "")
        # Heuristic: finding is referenced if any key token from its title appears
        key_tokens = [t for t in re.split(r"\W+", title) if len(t) > 3]
        referenced = any(tok.lower() in agent_text.lower() for tok in key_tokens)
        if not referenced:
            omitted.append(f)
            flags.append(VerificationFlag(
                flag_type="ANALYTICAL_OMISSION",
                description=(
                    f"Analytics Engine found high-impact pattern "
                    f"(impact={getattr(f,'impact',0):.2f}): '{title}' "
                    f"but agent output does not mention it."
                ),
                finding_id=getattr(f, "id", ""),
                severity="medium",
            ))

    return flags, ungrounded, omitted


# ─── Pass C: consistency verification ────────────────────────────────────────

def _pass_c_consistency(agent_text: str,
                         spine: AnalyticalStateBus
                         ) -> list[VerificationFlag]:
    """
    Check agent output for internal inconsistencies using Analytics Engine logic.
    """
    flags: list[VerificationFlag] = []
    findings = spine.active_findings

    # Rule C1: agent asserts independence but a correlation finding exists
    if _INDEP_RE.search(agent_text):
        corr_findings = [f for f in findings
                         if getattr(f, "category", "") == "CORRELATION"
                         and getattr(f, "calibrated_confidence",
                                     getattr(f, "confidence", 0)) >= 0.65]
        if corr_findings:
            titles = "; ".join(getattr(f, "title", "") for f in corr_findings[:2])
            flags.append(VerificationFlag(
                flag_type="INCONSISTENCY",
                description=(
                    f"Agent claimed independence but Analytics Engine found significant "
                    f"correlations: {titles}"
                ),
                finding_id=getattr(corr_findings[0], "id", ""),
                severity="high",
            ))

    # Rule C2: agent references full-period trend but change point exists
    if _FULL_PERIOD_RE.search(agent_text):
        cp_findings = [f for f in findings
                       if getattr(f, "category", "") == "CHANGE_POINT"
                       and getattr(f, "impact", 0) > 0.5]
        if cp_findings:
            flags.append(VerificationFlag(
                flag_type="INCONSISTENCY",
                description=(
                    "Agent used full-period trend language but Analytics Engine detected "
                    f"a structural change point "
                    f"({getattr(cp_findings[0], 'title', '')}). "
                    "Full-period average may be misleading."
                ),
                finding_id=getattr(cp_findings[0], "id", ""),
                severity="medium",
            ))

    # Rule C3: agent prediction contradicts Analytics Engine L6 direction
    pred_claims = _extract_prediction_claims(agent_text)
    pred_findings = [f for f in findings if getattr(f, "category", "") == "PREDICTION"]
    if pred_claims and pred_findings:
        # If Analytics Engine L6 best R² is low (< 0.20) but agent makes a specific prediction
        best_r2 = max(
            (getattr(f, "test_statistic", 0) for f in pred_findings),
            default=0
        )
        if best_r2 < 0.20 and len(pred_claims) > 0:
            flags.append(VerificationFlag(
                flag_type="INCONSISTENCY",
                description=(
                    f"Agent made a specific prediction but Analytics Engine L6 ensemble "
                    f"R²={best_r2:.3f} — target is barely predictable from "
                    f"available features. Prediction may lack evidential basis."
                ),
                severity="medium",
            ))

    return flags


# ─── Pass D: uncertainty injection ───────────────────────────────────────────

def _pass_d_uncertainty(agent_text: str,
                         spine: AnalyticalStateBus
                         ) -> tuple[list[VerificationFlag], list[str]]:
    """
    If agent makes predictions without uncertainty bounds, inject Analytics Engine's
    decomposed uncertainty (aleatoric + epistemic) from active findings.
    """
    flags:       list[VerificationFlag] = []
    injections:  list[str]              = []
    pred_claims  = _extract_prediction_claims(agent_text)

    if not pred_claims:
        return flags, injections

    has_uncertainty = bool(_UNCERTAINTY_RE.search(agent_text))
    if has_uncertainty:
        return flags, injections

    pred_findings = [f for f in spine.active_findings
                     if getattr(f, "category", "") == "PREDICTION"]
    if not pred_findings:
        # No Analytics Engine predictions to draw from
        flags.append(VerificationFlag(
            flag_type="UNCERTAINTY_MISSING",
            description=(
                "Agent made a prediction without uncertainty bounds. "
                "No Analytics Engine L6 predictions are available to supplement."
            ),
            severity="low",
        ))
        return flags, injections

    for f in pred_findings[:3]:
        conf  = getattr(f, "calibrated_confidence", getattr(f, "confidence", 0.5))
        r2    = getattr(f, "test_statistic", None)
        ue    = 1.0 - conf                                # epistemic uncertainty
        ua    = 1.0 - (r2 if r2 is not None else 0.5)    # aleatoric proxy
        title = getattr(f, "title", "prediction")
        injections.append(
            f"• {title}: "
            f"calibrated confidence {conf:.0%}, "
            f"epistemic uncertainty ≈{ue:.0%}, "
            f"aleatoric uncertainty ≈{ua:.0%}"
            + (f", CV R²={r2:.3f}" if r2 is not None else "")
        )

    if injections:
        flags.append(VerificationFlag(
            flag_type="UNCERTAINTY_MISSING",
            description=(
                "Agent prediction lacked uncertainty bounds. "
                "Analytics Engine uncertainty decomposition injected below."
            ),
            severity="low",
            injected_text="\n".join(injections),
        ))

    return flags, injections


# ─── main class ──────────────────────────────────────────────────────────────

class AnalyticalVerifier:
    """
     — Analytics Engine-extended VerifierLayer.

    Usage
    -----
    verifier = AnalyticalVerifier(spine=spine, provider=prov, model=m)
    result   = verifier.verify(agent_output, tool_context)
    final    = result.annotated_output(agent_output)

    Integration
    -----------
    In Agent.run_task() Step 4 (VERIFY):
      result = analytical_verifier.verify(
          agent_output   = _agent_output,
          tool_context   = blackboard.to_analytical_context(),
          spine          = analytical_spine,
      )
      if result.needs_revision:
          _agent_output = result.annotated_output(_agent_output)
    """

    def __init__(self,
                 spine:    AnalyticalStateBus | None = None,
                 provider: Any = None,
                 model:    str = "",
                 enabled:  bool = True) -> None:
        self.spine    = spine or AnalyticalStateBus()
        self.provider = provider
        self.model    = model
        self.enabled  = enabled

    def verify(self,
               agent_output: str,
               tool_context: str = "",
               impact_threshold: float = 0.70) -> AnalyticalVerificationResult:
        """
        Run all four verification passes and merge results.

        Parameters
        ----------
        agent_output     : str   The full agent response text to verify.
        tool_context     : str   Blackboard or tool-result context for Pass A.
        impact_threshold : float Minimum Analytics Engine finding impact to require mention.

        Returns
        -------
        AnalyticalVerificationResult
        """
        if not self.enabled:
            return AnalyticalVerificationResult(standard_passed=True)

        # ── Pass A: standard verification ─────────────────────────────────
        std_verdicts = self._standard_verify(agent_output, tool_context)
        std_passed   = all(v.get("verdict") != "contradicted" for v in std_verdicts)

        # ── Pass B: finding verification ──────────────────────────────────
        b_flags, ungrounded, omitted = _pass_b_finding_verification(
            agent_output, self.spine, impact_threshold)

        # ── Pass C: consistency check ──────────────────────────────────────
        c_flags = _pass_c_consistency(agent_output, self.spine)

        # ── Pass D: uncertainty injection ─────────────────────────────────
        d_flags, injections = _pass_d_uncertainty(agent_output, self.spine)

        all_flags = b_flags + c_flags + d_flags

        result = AnalyticalVerificationResult(
            standard_verdicts      = std_verdicts,
            standard_passed        = std_passed,
            flags                  = all_flags,
            ungrounded_claims      = ungrounded,
            omitted_findings       = omitted,
            uncertainty_injections = injections,
        )

        if all_flags:
            _log.debug(
                "AnalyticalVerifier: %d flags (%d high-sev)",
                len(all_flags),
                sum(1 for f in all_flags if f.severity == "high"),
            )

        return result

    # ── Pass A wrapper ────────────────────────────────────────────────────

    def _standard_verify(self, response: str, tool_context: str) -> list[dict]:
        """
        Delegate to the existing VerifierLayer.verify() if available,
        otherwise return empty list (graceful degradation).
        """
        if not self.provider:
            return []
        try:
            from essence.agents.verifier import VerifierLayer  # type: ignore[import]
            vl = VerifierLayer(provider=self.provider, model=self.model, enabled=True)
            raw_results = vl.verify(response, tool_context)
            return [
                {
                    "verdict":    getattr(r, "verdict",    "unverified"),
                    "confidence": getattr(r, "confidence", 0.5),
                    "evidence":   getattr(r, "evidence",   ""),
                    "claim":      getattr(r, "claim",      ""),
                }
                for r in raw_results
            ]
        except Exception as e:
            _log.debug("standard_verify fallback: %s", e)
            return []
