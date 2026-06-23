"""OpportunityEngine — surfaces latent opportunities from repeated signals.

When any topic crosses THRESHOLD observations, generates an OpportunityHint
and pushes it to ProactiveEngine as a synthetic ProactiveEvent.
Persistence: <workspace>/identity/opportunities.json
"""
from __future__ import annotations
import json, time, re
from pathlib import Path

THRESHOLD = 5

# ── Domain synthesis rules ────────────────────────────────────────────────────
# Each entry maps a topic substring to (signal_template, suggestion_template).
# Ordered from most specific to most general so the first match wins.
# This replaces the previous hardcoded two-entry dict.

_DOMAIN_RULES: list[tuple[str, str, str]] = [
    ("ai_agent",    "Repeated AI-agent research",      "Explore AI-agent productisation or tooling"),
    ("automation",  "Repeated workflow automation",    "Potential SaaS micro-product or internal tooling opportunity"),
    ("machine_learn","ML experimentation pattern",     "Consider formalising an ML pipeline or experiment tracker"),
    ("data_scienc", "Consistent data science work",    "Explore an open data project or Kaggle competition"),
    ("python",      "Python-first development pattern","Package a reusable library or publish a PyPI package"),
    ("javascript",  "JavaScript engagement pattern",   "Consider building an OSS frontend component or tool"),
    ("typescript",  "TypeScript engagement pattern",   "Consider building a typed library or API client"),
    ("writing",     "Consistent writing activity",     "Start a blog, newsletter, or publish an article"),
    ("research",    "Deep research engagement",        "Compile findings into a report or knowledge base"),
    ("finance",     "Finance domain interest",         "Build a personal finance tracker or analysis tool"),
    ("health",      "Health and wellness focus",       "Create a habit tracker or health dashboard"),
    ("productiv",   "Productivity focus area",         "Design a personal productivity system or tool"),
    ("security",    "Security domain interest",        "Run a security audit or contribute to a security OSS project"),
    ("devops",      "DevOps pattern",                  "Automate a deployment pipeline or write runbook documentation"),
    ("design",      "Design engagement pattern",       "Build a design system or style guide for a project"),
    ("business",    "Business domain interest",        "Draft a one-pager or explore a niche market opportunity"),
    ("educati",     "Education domain focus",          "Create a learning resource, course, or workshop"),
    ("game",        "Game development interest",       "Prototype a game mechanic or jam participation"),
    ("music",       "Music engagement",                "Start a music project, composition, or audio tool"),
    ("hardware",    "Hardware / embedded interest",    "Prototype an IoT device or contribute to an OSS hardware project"),
]


def _synthesize_suggestion(topic: str) -> tuple[str, str]:
    """
    Derive a (signal, suggestion) pair for any topic.

    Tries each rule in _DOMAIN_RULES by substring match (case-insensitive).
    Falls back to a well-formed generic suggestion if no rule matches.
    """
    topic_lc = topic.lower()
    for keyword, signal_tmpl, suggestion_tmpl in _DOMAIN_RULES:
        if keyword in topic_lc:
            signal     = signal_tmpl
            suggestion = suggestion_tmpl
            return signal, suggestion

    # Generic fallback: capitalise the topic and produce a meaningful suggestion
    label = re.sub(r"[_\-]", " ", topic).title()
    signal     = f"Repeated {label} activity"
    suggestion = f"Consider formalising your {label} work — publish, share, or automate it"
    return signal, suggestion


class OpportunityHint:
    __slots__ = ("domain", "signal", "suggestion", "confidence",
                 "first_seen", "last_seen")

    def __init__(self, domain: str, signal: str,
                 suggestion: str, confidence: float) -> None:
        self.domain     = domain
        self.signal     = signal
        self.suggestion = suggestion
        self.confidence = confidence
        self.first_seen = time.time()
        self.last_seen  = time.time()

    def to_dict(self) -> dict:
        return {s: getattr(self, s) for s in self.__slots__}


class OpportunityEngine:
    def __init__(self, workspace: Path) -> None:
        self._path   = workspace / "identity" / "opportunities.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._counts: dict[str, int]        = {}
        self._hints:  list[OpportunityHint] = []
        self._load()

    def observe(self, topic: str) -> list[OpportunityHint]:
        self._counts[topic] = self._counts.get(topic, 0) + 1
        new_hints: list[OpportunityHint] = []
        if self._counts[topic] == THRESHOLD:
            hint = self._synthesize(topic)
            self._hints.append(hint)
            new_hints.append(hint)
            self._persist()
        return new_hints

    def _synthesize(self, topic: str) -> OpportunityHint:
        signal, suggestion = _synthesize_suggestion(topic)
        # Confidence scales with how well-known the domain is
        known = any(k in topic.lower() for k, *_ in _DOMAIN_RULES)
        confidence = 0.65 if known else 0.45
        return OpportunityHint(topic, signal, suggestion, confidence)

    def active_hints(self) -> list[dict]:
        return [h.to_dict() for h in self._hints]

    # ── persistence ───────────────────────────────────────────────────────────

    def _persist(self) -> None:
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps([h.to_dict() for h in self._hints], indent=2),
                encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for d in raw:
                    h = OpportunityHint(
                        domain     = d.get("domain", ""),
                        signal     = d.get("signal", ""),
                        suggestion = d.get("suggestion", ""),
                        confidence = float(d.get("confidence", 0.4)),
                    )
                    h.first_seen = float(d.get("first_seen", time.time()))
                    h.last_seen  = float(d.get("last_seen",  time.time()))
                    self._hints.append(h)
            except Exception:
                pass
