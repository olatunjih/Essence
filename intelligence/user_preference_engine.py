"""UserPreferenceEngine — learns and applies per-user preferences from interaction history.

Tracked dimensions:
  • communication_style  : formal | casual | technical | concise | verbose
  • response_format      : prose | bullets | markdown | code-first | minimal
  • working_hours        : {start_hour, end_hour, timezone_offset}
  • preferred_language   : ISO-639-1 code (default "en")
  • interruption_level   : always | during_hours | never
  • detail_level         : high | medium | low
  • proactive_suggestions: bool
  • domains_of_interest  : list[str]  (topic clusters with frequency counts)

Preference signals are inferred from:
  - Message timestamps          → working_hours
  - Message length & vocabulary → communication_style / detail_level
  - Markdown usage              → response_format
  - Repeated topic keywords     → domains_of_interest
  - Explicit corrections ("be more concise", "use bullet points") → override

Persistence: <workspace>/identity/preferences.json
"""
from __future__ import annotations

import dataclasses
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any


# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULT_PREFS: dict[str, Any] = {
    "communication_style":   "balanced",   # formal|casual|technical|concise|verbose|balanced
    "response_format":       "markdown",   # prose|bullets|markdown|code-first|minimal
    "working_hours":         {"start": 8, "end": 20, "tz_offset": 0},
    "preferred_language":    "en",
    "interruption_level":    "during_hours",  # always|during_hours|never
    "detail_level":          "medium",         # high|medium|low
    "proactive_suggestions": True,
    "domains_of_interest":   {},               # {topic: count}
    "interaction_count":     0,
    "last_updated":          0.0,
    # Sliding-window stats for style inference
    "_avg_msg_len":          0.0,
    "_markdown_ratio":       0.0,
    "_hour_histogram":       [0] * 24,   # message counts per hour-of-day
}

# Regex patterns for explicit correction signals
_STYLE_OVERRIDES: list[tuple[re.Pattern, dict[str, Any]]] = [
    (re.compile(r"\b(be|use|make it|keep it)\s+(more\s+)?concise\b", re.I),
     {"communication_style": "concise", "detail_level": "low"}),
    (re.compile(r"\b(be|use|make it|keep it)\s+(more\s+)?detailed?\b", re.I),
     {"communication_style": "verbose", "detail_level": "high"}),
    (re.compile(r"\buse\s+bullet\s+points?\b", re.I),
     {"response_format": "bullets"}),
    (re.compile(r"\buse\s+(plain|prose|running)\s+text\b", re.I),
     {"response_format": "prose"}),
    (re.compile(r"\buse\s+markdown\b", re.I),
     {"response_format": "markdown"}),
    (re.compile(r"\bless\s+formal\b|\bcasual\b|\brelaxed\s+tone\b", re.I),
     {"communication_style": "casual"}),
    (re.compile(r"\bmore\s+formal\b|\bprofessional\s+tone\b", re.I),
     {"communication_style": "formal"}),
    (re.compile(r"\btechnical\b|\buse\s+technical\s+terms\b", re.I),
     {"communication_style": "technical"}),
    (re.compile(r"\bdon'?t\s+(send|give)\s+(me\s+)?(proactive|unsolicited)\b", re.I),
     {"proactive_suggestions": False}),
    (re.compile(r"\bproactive(ly)?\s+(suggest|surface|notify)\b", re.I),
     {"proactive_suggestions": True}),
    (re.compile(r"\bminimal\s+(output|response|format)\b", re.I),
     {"response_format": "minimal", "detail_level": "low"}),
]

# Common domain keywords → topic bucket
_DOMAIN_KEYWORDS: dict[str, str] = {
    "python":    "programming", "javascript": "programming", "code":  "programming",
    "typescript":"programming", "rust":       "programming",
    "budget":    "finance",     "invoice":    "finance",     "cost":  "finance",
    "meeting":   "productivity","calendar":   "productivity","task":  "productivity",
    "email":     "productivity","deadline":   "productivity",
    "research":  "research",    "study":      "research",    "paper": "research",
    "health":    "wellness",    "workout":    "wellness",    "sleep": "wellness",
    "write":     "writing",     "article":    "writing",     "blog":  "writing",
    "data":      "data-science","model":      "data-science","ml":    "data-science",
}

# EMA alpha for rolling stats
_ALPHA = 0.1


@dataclasses.dataclass
class PreferenceProfile:
    """Immutable snapshot of the current preference state."""
    communication_style:   str
    response_format:       str
    working_hours:         dict
    preferred_language:    str
    interruption_level:    str
    detail_level:          str
    proactive_suggestions: bool
    domains_of_interest:   dict[str, int]
    interaction_count:     int

    def is_working_hours(self) -> bool:
        """Return True if current (UTC-offset) hour is within working_hours."""
        import datetime
        now_h = (datetime.datetime.utcnow().hour +
                 self.working_hours.get("tz_offset", 0)) % 24
        return self.working_hours["start"] <= now_h < self.working_hours["end"]

    def top_domains(self, n: int = 5) -> list[str]:
        """Return top-N domains by interaction frequency."""
        return [k for k, _ in sorted(
            self.domains_of_interest.items(),
            key=lambda x: x[1], reverse=True
        )[:n]]

    def to_system_hint(self) -> str:
        """Return a compact system-prompt hint block for the LLM."""
        lines = [
            "=== USER PREFERENCES (apply silently) ===",
            f"Response format  : {self.response_format}",
            f"Communication    : {self.communication_style}",
            f"Detail level     : {self.detail_level}",
            f"Language         : {self.preferred_language}",
        ]
        if self.top_domains():
            lines.append(f"Interests        : {', '.join(self.top_domains(3))}")
        if not self.proactive_suggestions:
            lines.append("Proactive tips   : off — do not volunteer unsolicited suggestions")
        lines.append("==========================================")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class UserPreferenceEngine:
    """
    Observes user messages and system outcomes to maintain a living
    PreferenceProfile.  Thread-safe via a simple file-flush pattern.

    Integration points
    ------------------
    Boot       : instantiated in boot_kernel() after PersonalTwin
    ingest_capsule : call observe(raw_prompt) on every user message
    Agent._sys : call system_hint() to prepend preference block to system prompt
    MetaOrchestrator : call profile() to gate proactive suggestions
    """

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "identity" / "preferences.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._prefs: dict[str, Any] = self._load()

    # ── public API ────────────────────────────────────────────────────────────

    def observe(self, message: str, user_id: str = "user") -> None:
        """
        Update preference signals from a new user message.
        Should be called for every inbound user message.
        """
        p = self._prefs

        # --- explicit correction override check ---
        for pattern, overrides in _STYLE_OVERRIDES:
            if pattern.search(message):
                p.update(overrides)

        # --- EMA on message length ---
        ml = len(message.split())
        p["_avg_msg_len"] = (1 - _ALPHA) * p["_avg_msg_len"] + _ALPHA * ml

        # --- markdown usage ---
        has_md = bool(re.search(r"\*\*|`|#{1,3} |\|", message))
        p["_markdown_ratio"] = (1 - _ALPHA) * p["_markdown_ratio"] + _ALPHA * (1.0 if has_md else 0.0)

        # --- working hours inference via hour-of-day histogram ---
        import datetime
        hour = datetime.datetime.utcnow().hour
        hist = p["_hour_histogram"]
        hist[hour] += 1
        p["_hour_histogram"] = hist

        # --- domain keyword extraction ---
        words = re.findall(r"\b[a-z]{3,}\b", message.lower())
        domains: dict[str, int] = p.get("domains_of_interest", {})
        for w in words:
            if w in _DOMAIN_KEYWORDS:
                bucket = _DOMAIN_KEYWORDS[w]
                domains[bucket] = domains.get(bucket, 0) + 1
        p["domains_of_interest"] = domains

        # --- update inferred preferences every 5 observations ---
        p["interaction_count"] = p.get("interaction_count", 0) + 1
        if p["interaction_count"] % 5 == 0:
            self._infer_style(p)

        p["last_updated"] = time.time()
        self._save()

    def profile(self) -> PreferenceProfile:
        """Return current immutable preference snapshot."""
        p = self._prefs
        return PreferenceProfile(
            communication_style   = p.get("communication_style",   "balanced"),
            response_format       = p.get("response_format",       "markdown"),
            working_hours         = p.get("working_hours",         {"start": 8, "end": 20, "tz_offset": 0}),
            preferred_language    = p.get("preferred_language",    "en"),
            interruption_level    = p.get("interruption_level",    "during_hours"),
            detail_level          = p.get("detail_level",          "medium"),
            proactive_suggestions = p.get("proactive_suggestions", True),
            domains_of_interest   = p.get("domains_of_interest",   {}),
            interaction_count     = p.get("interaction_count",     0),
        )

    def system_hint(self) -> str:
        """Return a preference hint block suitable for injection into system prompts."""
        return self.profile().to_system_hint()

    def set(self, key: str, value: Any) -> None:
        """Explicitly override a preference field (e.g. from a settings API)."""
        allowed = set(_DEFAULT_PREFS.keys()) - {"_avg_msg_len", "_markdown_ratio",
                                                 "_hour_histogram", "interaction_count",
                                                 "last_updated"}
        if key not in allowed:
            raise ValueError(f"Unknown preference key: {key!r}")
        self._prefs[key] = value
        self._save()

    def reset(self) -> None:
        """Reset all preferences to defaults."""
        self._prefs = dict(_DEFAULT_PREFS)
        self._save()

    # ── inference helpers ─────────────────────────────────────────────────────

    def _infer_style(self, p: dict) -> None:
        """Re-derive style + detail-level from accumulated EMA stats."""
        avg_len = p["_avg_msg_len"]
        md_ratio = p["_markdown_ratio"]

        # Communication style from message verbosity
        if avg_len < 8:
            p["communication_style"] = "concise"
        elif avg_len > 50:
            p["communication_style"] = "verbose"
        elif md_ratio > 0.4:
            p["communication_style"] = "technical"

        # Response format from user's own markdown usage
        if md_ratio > 0.5:
            p["response_format"] = "markdown"
        elif md_ratio < 0.1 and avg_len < 15:
            p["response_format"] = "minimal"

        # Detail level from verbosity
        if avg_len > 60:
            p["detail_level"] = "high"
        elif avg_len < 10:
            p["detail_level"] = "low"
        else:
            p["detail_level"] = "medium"

        # Working hours from hour histogram
        hist = p["_hour_histogram"]
        total = sum(hist) or 1
        probs = [h / total for h in hist]
        active_hours = [h for h, prob in enumerate(probs) if prob > 0.05]
        if active_hours:
            wh = p.setdefault("working_hours", {"start": 8, "end": 20, "tz_offset": 0})
            wh["start"] = min(active_hours)
            wh["end"]   = max(active_hours) + 1

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                merged = dict(_DEFAULT_PREFS)
                merged.update(raw)
                return merged
            except Exception:
                pass
        return dict(_DEFAULT_PREFS)

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._prefs, indent=2))
        except Exception:
            pass
