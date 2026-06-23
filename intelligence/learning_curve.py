"""LearningCurveTracker — tracks skill growth and surfaces learning milestones.

Reads UserPreferenceEngine.profile().top_domains() and PersonalTwin
beliefs["skills"], tracks skill progression over time, surfaces learning
milestones, and recommends next steps via ResearchEngine.

Persistence: <workspace>/identity/learning_curve.json
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.intelligence.learning_curve")


# ── Milestones ────────────────────────────────────────────────────────────────

_MILESTONE_THRESHOLDS = [
    (5,   "Beginner",     "You've started exploring {domain}!"),
    (15,  "Curious",      "You're consistently engaging with {domain}."),
    (30,  "Developing",   "You're building a solid foundation in {domain}."),
    (60,  "Intermediate", "You have real working knowledge of {domain}."),
    (100, "Proficient",   "You've reached proficiency in {domain}!"),
    (200, "Advanced",     "You have advanced expertise in {domain}."),
    (400, "Expert",       "You are demonstrating expert-level engagement with {domain}."),
]


@dataclasses.dataclass
class DomainProgress:
    """Progress record for a single learning domain."""
    domain:           str
    interaction_count: int
    milestone_level:  str   # "Beginner" | "Curious" | ... | "Expert"
    last_milestone:   str   # timestamp ISO string of last milestone unlock
    first_seen:       float
    last_seen:        float
    recommended_next: list[str]   # topics suggested by ResearchEngine

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DomainProgress":
        return cls(**{k: v for k, v in d.items() if k in
                      {f.name for f in dataclasses.fields(cls)}})


@dataclasses.dataclass
class LearningMilestone:
    """A newly reached milestone event."""
    domain:    str
    level:     str
    message:   str
    reached_at: float


def _compute_milestone(count: int) -> tuple[str, str]:
    """Return (level_name, message_template) for the given count."""
    level_name = "Beginner"
    message    = _MILESTONE_THRESHOLDS[0][2]
    for threshold, name, tmpl in _MILESTONE_THRESHOLDS:
        if count >= threshold:
            level_name = name
            message    = tmpl
        else:
            break
    return level_name, message


class LearningCurveTracker:
    """
    Tracks skill growth across the user's top interest domains.

    Integration points
    ------------------
    Boot          : instantiated after UserPreferenceEngine and PersonalTwin
    ingest_capsule: call observe_domains(message, user_pref) on every user message
    Heartbeat     : call check_milestones() each tick to surface newly reached ones
    """

    def __init__(self, workspace: Path,
                 twin:        Any = None,
                 research:    Any = None,
                 event_bus:   Any = None) -> None:
        self._ws        = workspace
        self._twin      = twin
        self._research  = research
        self._bus       = event_bus
        self._path      = workspace / "identity" / "learning_curve.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._progress: dict[str, DomainProgress] = self._load()

    # ── public API ────────────────────────────────────────────────────────────

    def observe_domains(self, domains: list[str]) -> list[LearningMilestone]:
        """
        Increment interaction counts for each domain and check for
        newly reached milestones.  Returns list of newly unlocked milestones.
        """
        now       = time.time()
        new_milestones: list[LearningMilestone] = []

        for domain in domains:
            if not domain:
                continue
            prog = self._progress.get(domain)
            if prog is None:
                prog = DomainProgress(
                    domain            = domain,
                    interaction_count = 0,
                    milestone_level   = "Beginner",
                    last_milestone    = "",
                    first_seen        = now,
                    last_seen         = now,
                    recommended_next  = [],
                )
                self._progress[domain] = prog

            old_level = prog.milestone_level
            prog.interaction_count += 1
            prog.last_seen          = now
            new_level, tmpl = _compute_milestone(prog.interaction_count)

            if new_level != old_level:
                prog.milestone_level = new_level
                prog.last_milestone  = time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(now))
                message = tmpl.format(domain=domain)
                ms = LearningMilestone(
                    domain=domain, level=new_level,
                    message=message, reached_at=now)
                new_milestones.append(ms)
                self._publish_milestone(ms)
                # Update PersonalTwin skills axis
                if self._twin is not None:
                    try:
                        self._twin.update(
                            "skills", domain,
                            f"{new_level} ({prog.interaction_count} interactions)",
                            source="observed",
                        )
                    except Exception as exc:
                        log.debug("learning_twin_update_error: %s", exc)
                log.info("learning_milestone_reached",
                         extra={"domain": domain, "level": new_level})

        if new_milestones or domains:
            self._save()
        return new_milestones

    def check_milestones(self, user_pref: Any = None) -> list[LearningMilestone]:
        """
        Pull current top domains from UserPreferenceEngine and observe them
        (counts stay the same — this just ensures milestones are surfaced).
        Also refreshes recommended_next via ResearchEngine.
        """
        if user_pref is None:
            return []
        try:
            domains = user_pref.profile().top_domains(5)
        except Exception:
            return []
        if not domains:
            return []
        milestones = self.observe_domains(domains)
        self._refresh_recommendations(domains)
        return milestones

    def get_progress(self, domain: str) -> DomainProgress | None:
        """Return the progress record for a domain."""
        return self._progress.get(domain)

    def top_progress(self, n: int = 5) -> list[DomainProgress]:
        """Return top-N domains by interaction count."""
        return sorted(self._progress.values(),
                      key=lambda p: p.interaction_count, reverse=True)[:n]

    def summary(self) -> str:
        """Return a human-readable summary of learning progress."""
        top = self.top_progress(5)
        if not top:
            return "No learning domains tracked yet."
        lines = ["## Learning Progress"]
        for prog in top:
            bar_len = min(20, prog.interaction_count // 5)
            bar     = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(
                f"  {prog.domain:<20} [{bar}] "
                f"{prog.milestone_level} ({prog.interaction_count})"
            )
        return "\n".join(lines)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _refresh_recommendations(self, domains: list[str]) -> None:
        """Trigger ResearchEngine for top domains and store recommendations."""
        if self._research is None:
            return
        for domain in domains[:3]:
            prog = self._progress.get(domain)
            if prog is None:
                continue
            # Only refresh if recommendations are stale (> 24 h)
            last = prog.last_seen
            if time.time() - last < 86400 and prog.recommended_next:
                continue
            try:
                self._research.subscribe(domain)
                digests = self._research.run_cycle()
                recs: list[str] = []
                for d in digests:
                    if d.domain == domain:
                        recs = [i.get("title", "")[:80]
                                for i in d.items[:3] if i.get("title")]
                        break
                if recs:
                    prog.recommended_next = recs
                    self._save()
            except Exception as exc:
                log.debug("learning_research_error %s: %s", domain, exc)

    def _publish_milestone(self, ms: LearningMilestone) -> None:
        """Publish a milestone via event_bus."""
        if self._bus is None:
            return
        try:
            from essence.agents.proactive import WebhookEvent
            evt = WebhookEvent(
                source="learning",
                event_type="milestone",
                payload={
                    "domain":  ms.domain,
                    "level":   ms.level,
                    "message": ms.message,
                },
            )
            pub = getattr(self._bus, "publish", None)
            if callable(pub):
                pub(evt)
        except Exception as exc:
            log.debug("learning_milestone_publish_error: %s", exc)

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, DomainProgress]:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                return {d: DomainProgress.from_dict(v) for d, v in raw.items()}
            except Exception as exc:
                log.debug("learning_load_error: %s", exc)
        return {}

    def _save(self) -> None:
        try:
            data = {d: p.to_dict() for d, p in self._progress.items()}
            tmp  = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:
            log.debug("learning_save_error: %s", exc)
