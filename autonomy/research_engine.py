"""AutonomousResearchEngine — background domain monitoring and digest generation.

Reads monitored domains from PersonalTwin ("learning" axis).
Searches for new content each cycle, deduplicates, emits ResearchDigest.
Wire into HeartbeatScheduler as a periodic job (recommended: every 6h).
Persistence: <workspace>/logs/research_seen.json
"""
from __future__ import annotations
import dataclasses
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.autonomy.research_engine")


@dataclasses.dataclass
class ResearchDigest:
    domain:       str
    items:        list[dict]
    generated_at: float = dataclasses.field(default_factory=time.time)

    def to_proactive_body(self) -> str:
        lines = [f"Research digest: {self.domain}"]
        for item in self.items[:5]:
            lines.append(
                f"  \u2022 {item['title']} \u2014 {item.get('summary', '')[:120]}")
        return "\n".join(lines)


class AutonomousResearchEngine:
    def __init__(self, workspace: Path, twin: Any = None,
                 tool_belt: Any = None, router: Any = None) -> None:
        self._workspace      = workspace
        self._twin           = twin
        self._tool_belt      = tool_belt
        self._router         = router
        self._seen_path      = workspace / "logs" / "research_seen.json"
        self._seen_path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str]    = self._load_seen()
        self._subscriptions: list[str] = []

    def subscribe(self, domain: str) -> None:
        if domain not in self._subscriptions:
            self._subscriptions.append(domain)

    def _load_seen(self) -> set[str]:
        if self._seen_path.exists():
            try:
                return set(json.loads(self._seen_path.read_text()))
            except Exception:
                pass
        return set()

    def _save_seen(self) -> None:
        self._seen_path.write_text(json.dumps(list(self._seen)))

    def _url_hash(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def run_cycle(self) -> list[ResearchDigest]:
        domains = list(self._subscriptions)
        if self._twin:
            learning = self._twin.get("learning", "interests", "")
            if learning:
                domains += [t.strip() for t in learning.split(",")]
        digests: list[ResearchDigest] = []
        for domain in set(domains):
            items = self._fetch_domain(domain)
            new_items = [i for i in items
                         if self._url_hash(i.get("url", "")) not in self._seen]
            if new_items:
                for i in new_items:
                    self._seen.add(self._url_hash(i.get("url", "")))
                digests.append(ResearchDigest(domain=domain, items=new_items))
                log.info("research_digest: %s \u2014 %d new items",
                         domain, len(new_items))
        self._save_seen()
        return digests

    def _fetch_domain(self, domain: str) -> list[dict]:
        """Fetch new items for *domain*.

        Tries tool_belt.search() first; falls back to an LLM router call
        when no search-capable tool is available.
        """
        if self._tool_belt and hasattr(self._tool_belt, "search"):
            try:
                return self._tool_belt.search(
                    f"latest {domain} research 2026") or []
            except Exception as exc:
                log.warning("research_fetch_failed %s: %s", domain, exc)

        if self._router is not None:
            try:
                result = self._router.complete(
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Search for recent developments about: {domain}. "
                            "Return a JSON array of objects with keys: "
                            "title (string), url (string), summary (string). "
                            "Include 3-5 items. Respond with ONLY the JSON array."
                        ),
                    }],
                    model="general",
                    max_tokens=800,
                    seed=42,
                )
                import re
                m = re.search(r"\[.*\]", result, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
            except Exception as exc:
                log.warning("research_fetch_llm_fallback_failed %s: %s", domain, exc)

        log.debug("research_engine: no search capability for domain %s", domain)
        return []
