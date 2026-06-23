"""DomainLensManager domain abstraction + AnalyticalStateBus bus."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.analytics.models import DomainLens  # noqa: F401  [fix: used below without import]

# Domain Lens DOMAIN-AGNOSTIC ABSTRACTION
# ══════════════════════════════════════════════════════════════════════════════

class DomainLensManager:
    """Universal Domain-Agnostic Abstraction Layer."""

    def __init__(self):
        self.lenses = {} # name -> DomainLens

    def register_lens(self, lens: DomainLens):
        self.lenses[lens.name] = lens

    def auto_detect_domain(self, df: Any, schema: dict) -> DomainLens | None:
        """5.3 Domain Auto-Detection logic."""
        best_lens = None
        best_score = 0.0

        col_names = [c.lower() for c in df.columns]

        for lens in self.lenses.values():
            score = 0.0
            # Simple column name matching
            for classifier in lens.feature_classifiers:
                if any(classifier.get("pattern", "").lower() in c for c in col_names):
                    score += 0.2

            if score > best_score:
                best_score = score
                best_lens = lens

        return best_lens if best_score > 0.4 else None

    def inject_lens(self, findings: list, lens: DomainLens | None) -> list:
        """5.4 — Infect mathematical analysis with domain meaning.
        Annotates each Finding.domain with the active lens name, a domain-templated
        narrative (when the lens defines one for that category/sub_category), any
        matching anomaly/relationship interpretation, and whether the finding clears
        the lens's domain-specific significance threshold for its category."""
        if not lens or not findings:
            return findings
        interpreters = (lens.anomaly_interpreters or []) + (lens.relationship_interpreters or [])
        for f in findings:
            tag: dict = {"lens": lens.name}

            template = (lens.narrative_templates.get(f.sub_category)
                        or lens.narrative_templates.get(f.category))
            if template:
                try:
                    tag["narrative"] = template.format(
                        title=f.title, description=f.description,
                        category=f.category, sub_category=f.sub_category)
                except Exception:
                    tag["narrative"] = template

            for interp in interpreters:
                if interp.get("sub_category") in (f.sub_category, f.category):
                    tag["interpretation"] = interp.get("meaning", "")
                    break

            threshold = lens.threshold_overrides.get(f.category)
            if threshold is not None:
                tag["domain_significant"] = bool(f.impact >= threshold)

            f.domain = {**(f.domain or {}), **tag}
        return findings


def default_lenses() -> list[DomainLens]:
    """A small set of illustrative starter lenses spanning the three domains used
    throughout the wiring spec (trade/finance, sports, medical). These exist so
    auto_detect_domain() has something to match against out of the box; add more
    via register_lens() — no subsystem code changes required."""
    return [
        DomainLens(
            name="trade",
            description="Financial markets / trading data",
            feature_classifiers=[{"pattern": p} for p in
                ("price", "return", "volume", "ticker", "spread", "yield", "pnl")],
            threshold_overrides={"CORRELATIONS": 0.3, "ANOMALIES": 0.6},
            narrative_templates={
                "CORRELATIONS": "Risk-relevant relationship: {description}",
                "ANOMALIES": "Potential regime shift or tail event: {description}",
            },
            relationship_interpreters=[{"sub_category": "threshold", "meaning":
                "Possible non-linear market regime boundary; expect fat tails (Student-t), not Normal."}],
            anomaly_interpreters=[{"sub_category": "outlier", "meaning":
                "Could be a tail event — verify against market-efficiency literature before trusting."}],
        ),
        DomainLens(
            name="sports",
            description="Sports performance / matchup data",
            feature_classifiers=[{"pattern": p} for p in
                ("team", "match", "score", "goal", "win", "player", "season")],
            threshold_overrides={"CORRELATIONS": 0.35, "ENTITIES": 0.4},
            narrative_templates={
                "ENTITIES": "Entity archetype detected: {description}",
                "CORRELATIONS": "Matchup-relevant relationship: {description}",
            },
            relationship_interpreters=[{"sub_category": "interaction", "meaning":
                "Likely a matchup effect — model entity interaction, not independent draws."}],
        ),
        DomainLens(
            name="medical",
            description="Clinical / treatment outcome data",
            feature_classifiers=[{"pattern": p} for p in
                ("patient", "treatment", "diagnosis", "dose", "outcome", "symptom")],
            threshold_overrides={"CAUSAL": 0.25, "ANOMALIES": 0.5},
            narrative_templates={
                "CAUSAL": "Possible treatment effect (confounders not yet ruled out): {description}",
                "ANOMALIES": "Possible adverse-event signal: {description}",
            },
            anomaly_interpreters=[{"sub_category": "outlier", "meaning":
                "Cross-check against clinical guidelines before treating as a true signal."}],
        ),
    ]


def register_default_lenses(nexus: "DomainLensManager") -> None:
    """Convenience: register the illustrative starter lenses onto a DomainLensManager."""
    for lens in default_lenses():
        nexus.register_lens(lens)

# ══════════════════════════════════════════════════════════════════════════════
