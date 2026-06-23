"""
SkillRepository — central, persistent registry for all Essence skills.

Design goals:
• Single source of truth: every skill (local, remote, MCP, learned) is
  registered here and addressable by name.
• Persistence: skills are serialised as SKILL.md files on disk inside
  workspace/skills/<name>/SKILL.md so they survive restarts.
• Search: fuzzy relevance ranking with optional category/tag filters.
• Versioning: newer registrations with a higher semver replace older ones;
  downgrades are rejected unless force=True.
• Thread-safe: a single reentrant lock guards all mutations.
"""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.skills.models import (
    SkillSpec, SkillResult, SkillSource, SkillStatus, SkillType,
    spec_from_skill_md,
)
import dataclasses as _dc
import json as _json
import re as _re
import time as _time
import threading as _threading
from pathlib import Path
from typing import Any, Callable, Iterator

log = logging.getLogger("essence.skills.repository")

# ══════════════════════════════════════════════════════════════════════════════
# Semver comparison helper
# ══════════════════════════════════════════════════════════════════════════════

def _parse_semver(ver: str) -> tuple[int, int, int]:
    parts = _re.findall(r"\d+", ver or "")
    while len(parts) < 3:
        parts.append("0")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _semver_gt(a: str, b: str) -> bool:
    """Return True if semver a > b."""
    return _parse_semver(a) > _parse_semver(b)


# ══════════════════════════════════════════════════════════════════════════════
# Repository
# ══════════════════════════════════════════════════════════════════════════════

class SkillRepository:
    """
    Central registry and persistence layer for all Essence skills.

    Lifecycle:
      repo = SkillRepository(workspace)
      repo.load_from_disk()          # hydrates from workspace/skills/
      repo.register(spec)            # adds/updates a skill
      skills = repo.search("code review")
      repo.flush()                   # writes any dirty spec back to disk
    """

    _INDEX_FILE = "skills_index.json"  # repo-level index inside workspace/skills/

    def __init__(self, workspace: Path) -> None:
        self._workspace  = workspace
        self._skills_dir = workspace / "skills"
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._registry: dict[str, SkillSpec] = {}  # name → SkillSpec
        self._dirty:    set[str]             = set()
        self._lock      = _threading.RLock()

    # ── Public API ────────────────────────────────────────────────────────────

    def load_from_disk(self) -> int:
        """
        Scan workspace/skills/ and load all SKILL.md files into the registry.
        Returns the number of skills loaded.
        """
        loaded = 0
        with self._lock:
            for skill_md in self._skills_dir.glob("*/SKILL.md"):
                name = skill_md.parent.name
                try:
                    content = skill_md.read_text(encoding="utf-8")
                    spec = spec_from_skill_md(
                        md_content = content,
                        skill_name = name,
                        skill_path = str(skill_md),
                        source     = SkillSource.LOCAL,
                    )
                    self._registry[spec.name] = spec
                    loaded += 1
                except Exception as exc:
                    log.debug("skill_load_failed",
                              extra={"name": name, "error": str(exc)[:80]})
        log.info("skill_repository_loaded", extra={"count": loaded})
        return loaded

    def register(self,
                 spec:  SkillSpec,
                 force: bool = False) -> tuple[bool, str]:
        """
        Register or update a skill.

        Versioning:
          • If the skill doesn't exist, it is added.
          • If it exists and the new version is higher, it is updated.
          • If it exists and the new version is equal or lower, the call is
            a no-op unless force=True.

        Returns (success, message).
        """
        with self._lock:
            existing = self._registry.get(spec.name)
            if existing is not None and not force:
                if not _semver_gt(spec.version, existing.version):
                    return False, (
                        f"Skill '{spec.name}' v{existing.version} already "
                        f"registered; new v{spec.version} is not newer."
                    )
            self._registry[spec.name] = spec
            self._dirty.add(spec.name)
            log.info("skill_registered",
                     extra={"name": spec.name, "version": spec.version,
                            "source": spec.source.value})
            return True, f"Skill '{spec.name}' v{spec.version} registered."

    def unregister(self, name: str) -> tuple[bool, str]:
        """Remove a skill from the in-memory registry (does NOT delete from disk)."""
        with self._lock:
            if name not in self._registry:
                return False, f"Skill '{name}' not found."
            del self._registry[name]
            self._dirty.discard(name)
            return True, f"Skill '{name}' unregistered."

    def get(self, name: str) -> SkillSpec | None:
        with self._lock:
            return self._registry.get(name)

    def all(self) -> list[SkillSpec]:
        with self._lock:
            return list(self._registry.values())

    def count(self) -> int:
        with self._lock:
            return len(self._registry)

    def search(self,
               query:    str,
               category: str  | None = None,
               tags:     list[str] | None = None,
               source:   SkillSource | None = None,
               status:   SkillStatus        = SkillStatus.ACTIVE,
               limit:    int                = 10,
               min_score: float             = 0.05) -> list[SkillSpec]:
        """
        Return up to `limit` skills ranked by relevance to `query`.

        Filtering:
          category  — exact match (case-insensitive)
          tags      — skill must have ALL specified tags
          source    — filter by origin (local, remote, mcp, learned)
          status    — default ACTIVE; pass None to include all statuses
        """
        with self._lock:
            candidates = list(self._registry.values())

        # Filter
        if status is not None:
            candidates = [s for s in candidates if s.status == status]
        if category is not None:
            candidates = [s for s in candidates
                          if s.category.lower() == category.lower()]
        if tags:
            tags_lower = {t.lower() for t in tags}
            candidates = [s for s in candidates
                          if tags_lower.issubset({t.lower() for t in s.tags})]
        if source is not None:
            candidates = [s for s in candidates if s.source == source]

        if not query.strip():
            return candidates[:limit]

        # Score + rank
        scored = [(s, s.matches_query(query)) for s in candidates]
        scored = [(s, sc) for s, sc in scored if sc >= min_score]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:limit]]

    def deprecate(self, name: str) -> tuple[bool, str]:
        with self._lock:
            spec = self._registry.get(name)
            if spec is None:
                return False, f"Skill '{name}' not found."
            spec.status = SkillStatus.DEPRECATED
            self._dirty.add(name)
            return True, f"Skill '{name}' deprecated."

    def record_usage(self, name: str, elapsed_ms: float, ok: bool) -> None:
        """Update runtime telemetry for a skill (use_count, avg_latency, error_rate)."""
        with self._lock:
            spec = self._registry.get(name)
            if spec is None:
                return
            n = spec.use_count
            spec.use_count    += 1
            spec.last_used_at  = _time.time()
            # Exponential moving average for latency
            alpha             = 0.1
            spec.avg_latency_ms = (
                elapsed_ms if n == 0
                else spec.avg_latency_ms * (1 - alpha) + elapsed_ms * alpha
            )
            # Error rate: cumulative ratio
            prev_errors = round(spec.error_rate * n)
            spec.error_rate = (prev_errors + (0 if ok else 1)) / (n + 1)

    # ── Persistence ───────────────────────────────────────────────────────────

    def flush(self) -> int:
        """
        Write all dirty SkillSpecs back to disk as SKILL.md files.
        Returns the number of skills written.
        """
        written = 0
        with self._lock:
            dirty_names = list(self._dirty)
            self._dirty.clear()

        for name in dirty_names:
            spec = self._registry.get(name)
            if spec is None:
                continue
            try:
                skill_dir = self._skills_dir / _sanitise_name(name)
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(
                    spec.to_skill_md(), encoding="utf-8")
                written += 1
                log.debug("skill_flushed", extra={"name": name})
            except Exception as exc:
                log.warning("skill_flush_failed",
                            extra={"name": name, "error": str(exc)[:80]})
        return written

    def save_skill(self, spec: SkillSpec) -> bool:
        """Write a single SkillSpec to disk immediately."""
        try:
            skill_dir = self._skills_dir / _sanitise_name(spec.name)
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                spec.to_skill_md(), encoding="utf-8")
            return True
        except Exception as exc:
            log.warning("skill_save_failed",
                        extra={"name": spec.name, "error": str(exc)[:80]})
            return False

    def delete_from_disk(self, name: str) -> tuple[bool, str]:
        """Remove skill directory from disk AND unregister from memory."""
        import shutil as _shutil
        safe = _sanitise_name(name)
        skill_dir = self._skills_dir / safe
        if not skill_dir.exists():
            self.unregister(name)
            return True, f"Skill '{name}' removed (was not on disk)."
        try:
            _shutil.rmtree(skill_dir)
            self.unregister(name)
            return True, f"Skill '{name}' deleted from disk."
        except Exception as exc:
            return False, str(exc)[:120]

    # ── Index / introspection ─────────────────────────────────────────────────

    def summary_index(self) -> str:
        """
        Returns a compact text index suitable for injection into a system prompt.
        Format:  • <name> (<category>): <description>
        """
        with self._lock:
            specs = [s for s in self._registry.values()
                     if s.status == SkillStatus.ACTIVE]
        if not specs:
            return ""
        lines = ["[Available Skills — call skill_execute to invoke]"]
        for s in sorted(specs, key=lambda x: x.name):
            lines.append(f"  • {s.name} ({s.category}): {s.description[:80]}")
        return "\n".join(lines)

    def to_json_index(self) -> str:
        """JSON-serialisable index (name, description, category, tags, source)."""
        with self._lock:
            specs = list(self._registry.values())
        index = [
            {
                "name":        s.name,
                "description": s.description[:120],
                "category":    s.category,
                "tags":        s.tags,
                "skill_type":  s.skill_type.value,
                "source":      s.source.value,
                "status":      s.status.value,
                "version":     s.version,
                "use_count":   s.use_count,
                "avg_latency_ms": round(s.avg_latency_ms, 1),
                "error_rate":  round(s.error_rate, 3),
            }
            for s in specs
        ]
        return _json.dumps(index, indent=2)

    def import_from_url(self, url: str, name: str | None = None) -> tuple[bool, str]:
        """
        Fetch a raw SKILL.md from an HTTPS URL and register + persist it.
        Includes SSRF guard (rejects RFC-1918 / loopback destinations).
        """
        import urllib.request as _req
        import urllib.parse   as _urlparse
        import socket         as _socket
        import ipaddress      as _ipaddress

        parsed = _urlparse.urlparse(url)
        if parsed.scheme.lower() != "https":
            return False, "Only HTTPS URLs are accepted."

        hostname = parsed.hostname or ""
        if not hostname:
            return False, "URL has no resolvable hostname."

        try:
            addr_infos = _socket.getaddrinfo(hostname, None)
            for _, _, _, _, sockaddr in addr_infos:
                ip = _ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return False, (
                        f"Blocked: '{hostname}' resolves to a private/reserved "
                        f"address ({ip})."
                    )
        except Exception as exc:
            return False, f"Hostname resolution failed: {exc}"

        try:
            with _req.urlopen(url, timeout=15) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return False, f"HTTP fetch failed: {exc}"

        if name is None:
            url_path = url.rstrip("/").split("/")[-1]
            name = _re.sub(r"\.[a-zA-Z]+$", "", url_path)
            name = _sanitise_name(name) or "remote_skill"

        spec = spec_from_skill_md(
            md_content = raw,
            skill_name = name,
            skill_path = "",
            source     = SkillSource.REMOTE,
        )
        ok, msg = self.register(spec, force=True)
        if ok:
            spec.skill_path = str(
                self._skills_dir / _sanitise_name(spec.name) / "SKILL.md")
            self.save_skill(spec)
        return ok, msg


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _sanitise_name(name: str) -> str:
    return _re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip()).strip("_") or "skill"
