"""
PromptManager — persistent prompt storage with usage tracking and
progressive discovery.

Stores prompts in workspace/prompts/prompts.json.  Each record tracks:
  - text, title, tags, category
  - use_count, last_used_at, created_at
  - source: 'user' | 'suggested' | 'learned'

Progressive discovery:
  - record_usage(text)    — called each time a prompt is sent
  - suggest(limit)        — returns prompts ranked by use_count + recency
  - mine_from_history(messages) — extracts reusable patterns from chat history
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.prompts.manager")


@dataclass
class PromptRecord:
    id: str
    title: str
    text: str
    tags: list[str] = field(default_factory=list)
    category: str = "general"
    source: str = "user"
    use_count: int = 0
    last_used_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    pinned: bool = False

    def score(self) -> float:
        """Composite score for progressive ranking."""
        recency = max(0.0, 1.0 - (time.time() - self.last_used_at) / 604800)
        return self.use_count * 1.5 + recency * 5 + (10 if self.pinned else 0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PromptRecord":
        return cls(
            id=d.get("id", _make_id(d.get("text", ""))),
            title=d.get("title", ""),
            text=d.get("text", ""),
            tags=d.get("tags", []),
            category=d.get("category", "general"),
            source=d.get("source", "user"),
            use_count=d.get("use_count", 0),
            last_used_at=d.get("last_used_at", 0.0),
            created_at=d.get("created_at", time.time()),
            pinned=d.get("pinned", False),
        )


def _make_id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


# ── Built-in seed prompts (shown on first run) ────────────────────────────────
_SEED_PROMPTS: list[dict] = [
    {
        "title": "Analyze data",
        "text": "Analyze the data and summarize key findings with charts.",
        "tags": ["data", "analysis"],
        "category": "analysis",
        "source": "suggested",
    },
    {
        "title": "Write Python script",
        "text": "Write a Python script to accomplish the following task:",
        "tags": ["code", "python"],
        "category": "code",
        "source": "suggested",
    },
    {
        "title": "Search recent papers",
        "text": "Search for recent research papers on this topic and summarize the key findings.",
        "tags": ["research", "papers"],
        "category": "research",
        "source": "suggested",
    },
    {
        "title": "Explain error log",
        "text": "Explain what this error means and suggest how to fix it:",
        "tags": ["debug", "errors"],
        "category": "debug",
        "source": "suggested",
    },
    {
        "title": "Refactor code",
        "text": "Refactor this code to be cleaner, more efficient, and well-documented:",
        "tags": ["code", "refactor"],
        "category": "code",
        "source": "suggested",
    },
    {
        "title": "Draft email",
        "text": "Draft a professional email about the following topic:",
        "tags": ["writing", "email"],
        "category": "writing",
        "source": "suggested",
    },
]


class PromptManager:
    """
    Manages the prompt library with usage tracking and progressive discovery.

    Thread-safe: all mutations are protected by self._lock.
    """

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "prompts" / "prompts.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._prompts: dict[str, PromptRecord] = {}
        self._load()
        self._maybe_seed()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for d in raw:
                r = PromptRecord.from_dict(d)
                self._prompts[r.id] = r
        except Exception as exc:
            log.warning("prompt_load_error", extra={"error": str(exc)[:80]})

    def _save(self) -> None:
        try:
            rows = [p.to_dict() for p in self._prompts.values()]
            self._path.write_text(
                json.dumps(rows, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("prompt_save_error", extra={"error": str(exc)[:80]})

    def _maybe_seed(self) -> None:
        if self._prompts:
            return
        for d in _SEED_PROMPTS:
            rec = PromptRecord(
                id=_make_id(d["text"]),
                title=d["title"],
                text=d["text"],
                tags=d.get("tags", []),
                category=d.get("category", "general"),
                source=d.get("source", "suggested"),
            )
            self._prompts[rec.id] = rec
        self._save()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(self, title: str, text: str,
               tags: list[str] | None = None,
               category: str = "general",
               source: str = "user") -> PromptRecord:
        pid = _make_id(text)
        with self._lock:
            if pid in self._prompts:
                return self._prompts[pid]
            rec = PromptRecord(
                id=pid, title=title, text=text,
                tags=tags or [], category=category, source=source)
            self._prompts[pid] = rec
            self._save()
        return rec

    def get(self, prompt_id: str) -> PromptRecord | None:
        return self._prompts.get(prompt_id)

    def update(self, prompt_id: str, **kwargs: Any) -> PromptRecord | None:
        with self._lock:
            rec = self._prompts.get(prompt_id)
            if rec is None:
                return None
            allowed = {"title", "text", "tags", "category", "pinned"}
            for k, v in kwargs.items():
                if k in allowed:
                    setattr(rec, k, v)
            self._save()
        return rec

    def delete(self, prompt_id: str) -> bool:
        with self._lock:
            if prompt_id not in self._prompts:
                return False
            del self._prompts[prompt_id]
            self._save()
        return True

    def list_all(self, category: str | None = None,
                 source: str | None = None) -> list[PromptRecord]:
        with self._lock:
            rows = list(self._prompts.values())
        if category:
            rows = [r for r in rows if r.category == category]
        if source:
            rows = [r for r in rows if r.source == source]
        return sorted(rows, key=lambda r: r.score(), reverse=True)

    # ── Usage tracking ────────────────────────────────────────────────────────

    def record_usage(self, text: str) -> None:
        """
        Called each time a message is sent.  If the text (or a near-match)
        exists in the library, increment use_count and last_used_at.
        Also auto-promote high-frequency messages into 'learned' prompts.
        """
        stripped = text.strip()
        if not stripped:
            return
        pid = _make_id(stripped)
        with self._lock:
            if pid in self._prompts:
                self._prompts[pid].use_count += 1
                self._prompts[pid].last_used_at = time.time()
                self._save()
            else:
                self._auto_learn(stripped)

    def _auto_learn(self, text: str) -> None:
        """
        Track unsaved messages in a lightweight frequency counter and
        promote any message seen ≥ 3 times to a learned prompt.
        """
        if not hasattr(self, "_freq"):
            self._freq: dict[str, int] = {}
        key = text[:200]
        self._freq[key] = self._freq.get(key, 0) + 1
        if self._freq[key] >= 3:
            pid = _make_id(key)
            if pid not in self._prompts:
                title = (key[:60] + "…") if len(key) > 60 else key
                rec = PromptRecord(
                    id=pid,
                    title=title,
                    text=key,
                    tags=["learned"],
                    category="learned",
                    source="learned",
                    use_count=self._freq[key],
                    last_used_at=time.time(),
                )
                self._prompts[pid] = rec
                self._save()
                log.info("prompt_auto_learned", extra={"title": title[:60]})

    # ── Progressive suggestions ───────────────────────────────────────────────

    def suggest(self, limit: int = 8, context: str = "") -> list[PromptRecord]:
        """
        Return top prompts ranked by (usage frequency × recency × context match).
        If context is given, boosts prompts whose text contains context keywords.
        """
        rows = self.list_all()
        if context:
            kws = set(re.split(r"\W+", context.lower())) - {"", "the", "a", "an"}
            def boost(r: PromptRecord) -> float:
                hits = sum(1 for w in kws if w in r.text.lower() or any(w in t for t in r.tags))
                return r.score() + hits * 3
            rows = sorted(rows, key=boost, reverse=True)
        return rows[:limit]

    # ── Pattern mining from chat history ─────────────────────────────────────

    def mine_from_history(self, messages: list[dict]) -> int:
        """
        Extract reusable prompt patterns from chat history.

        messages: list of {"role": str, "content": str}

        Looks for user messages that:
          - are ≥ 20 chars long (not trivial)
          - end with a colon (template-like)
          - contain explicit template markers ({placeholder}, [fill this])

        Returns count of new prompts learned.
        """
        TEMPLATE_RE = re.compile(r"\{[a-z_]+\}|\[[^\]]+\]|<[a-z_]+>", re.I)
        learned = 0
        for msg in messages:
            if msg.get("role") != "user":
                continue
            text = str(msg.get("content", "")).strip()
            if len(text) < 20:
                continue
            if TEMPLATE_RE.search(text) or text.endswith(":"):
                pid = _make_id(text)
                if pid not in self._prompts:
                    title = (text[:60] + "…") if len(text) > 60 else text
                    rec = PromptRecord(
                        id=pid, title=title, text=text,
                        tags=["mined"], category="learned", source="learned")
                    with self._lock:
                        self._prompts[pid] = rec
                        self._save()
                    learned += 1
        return learned

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            rows = list(self._prompts.values())
        total = len(rows)
        by_src = {}
        for r in rows:
            by_src[r.source] = by_src.get(r.source, 0) + 1
        most_used = sorted(rows, key=lambda r: r.use_count, reverse=True)[:3]
        return {
            "total": total,
            "by_source": by_src,
            "most_used": [{"title": r.title, "use_count": r.use_count} for r in most_used],
        }
