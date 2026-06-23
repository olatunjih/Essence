"""
SkillDiscovery — finds and imports skills from multiple sources.

Sources supported:
  1. Local workspace/skills/ directory (watches for new additions)
  2. Remote HTTPS URLs / raw GitHub Gist / GitLab snippet
  3. MCP servers (via tool-list protocol) — extracts tool specs → SkillSpec
  4. Observed agent behaviour patterns — mines repeated tool call sequences
  5. OpenAI / Anthropic / community skill registries (well-known index URLs)

All discovered skills are handed to a SkillRepository for deduplication,
versioning, and persistence.
"""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.skills.models import (
    SkillSpec, SkillSource, SkillStatus, SkillType,
    SkillInputSpec, SkillOutputSpec, SkillGuardrails,
    spec_from_skill_md,
)
import json       as _json
import re         as _re
import time       as _time
import threading  as _threading
import urllib.request  as _req
import urllib.parse    as _urlparse
from pathlib import Path
from typing  import Any

log = logging.getLogger("essence.skills.discovery")

# ── Known community index endpoints ──────────────────────────────────────────
# Each entry is an HTTPS URL returning a JSON array of skill records OR raw
# SKILL.md paths.  Add entries for any trusted registry here.
_COMMUNITY_INDICES: list[str] = [
    # MCP servers index (JSON array with {name, description, url} per tool)
    # Intentionally left sparse — populated via Essence_SKILL_REGISTRIES env var.
]

# Allow operators to inject additional registry URLs
_env_registries = os.environ.get("Essence_SKILL_REGISTRIES", "")
if _env_registries:
    for _r in _env_registries.split(","):
        _r = _r.strip()
        if _r.startswith("https://"):
            _COMMUNITY_INDICES.append(_r)


# ══════════════════════════════════════════════════════════════════════════════
# SkillDiscovery
# ══════════════════════════════════════════════════════════════════════════════

class SkillDiscovery:
    """
    Discovers skills from local, remote, MCP, and pattern sources, then
    registers them into the supplied SkillRepository.

    Usage:
        disc = SkillDiscovery(workspace, repository)
        disc.scan_local()          # walk workspace/skills/
        disc.scan_mcp(mcp_client)  # pull tools from an MCP server
        disc.scan_remote_index()   # pull from community registries
        disc.scan_all()            # all of the above
    """

    def __init__(self, workspace: Path, repository: Any) -> None:
        self._workspace    = workspace
        self._repo         = repository   # SkillRepository
        self._lock         = _threading.Lock()
        self._seen_hashes: set[str] = set()
        self._usage_counts: dict[str, int]   = {}
        self._usage_last:   dict[str, float] = {}
        self._load_usage()

    # ── 1. Local scan ─────────────────────────────────────────────────────────

    def scan_local(self) -> int:
        """
        Walk workspace/skills/ and register any SKILL.md not already known.
        Returns the count of newly registered skills.
        """
        skills_dir = self._workspace / "skills"
        if not skills_dir.exists():
            return 0

        registered = 0
        for skill_md in skills_dir.glob("*/SKILL.md"):
            name = skill_md.parent.name
            try:
                content = skill_md.read_text(encoding="utf-8")
                h       = hashlib.sha256(content.encode()).hexdigest()[:16]
                if h in self._seen_hashes:
                    continue
                spec = spec_from_skill_md(
                    md_content = content,
                    skill_name = name,
                    skill_path = str(skill_md),
                    source     = SkillSource.LOCAL,
                )
                ok, _ = self._repo.register(spec)
                if ok:
                    self._seen_hashes.add(h)
                    registered += 1
            except Exception as exc:
                log.debug("local_skill_scan_error",
                          extra={"name": name, "error": str(exc)[:80]})

        log.info("skill_discovery_local", extra={"registered": registered})
        return registered

    # ── 2. MCP server scan ───────────────────────────────────────────────────

    def scan_mcp(self, mcp_client: Any) -> int:
        """
        Pull the tool list from an MCP client instance and convert each tool
        into a SkillSpec with source=MCP.

        The MCP client is expected to expose:
          mcp_client.list_tools() -> list of dicts with keys:
            name, description, inputSchema (JSON Schema dict)

        Returns the number of newly registered skills.
        """
        if mcp_client is None:
            return 0
        try:
            tools: list[dict] = mcp_client.list_tools()
        except Exception as exc:
            log.debug("mcp_list_tools_failed", extra={"error": str(exc)[:80]})
            return 0

        registered = 0
        for tool in tools:
            try:
                tname = str(tool.get("name", "")).strip()
                if not tname:
                    continue
                in_schema = tool.get("inputSchema", {}) or {}
                in_spec   = SkillInputSpec(
                    properties  = in_schema.get("properties", {}),
                    required    = in_schema.get("required",   []),
                    description = in_schema.get("description",""),
                )
                spec = SkillSpec(
                    name        = f"mcp_{tname}",
                    description = str(tool.get("description", tname))[:200],
                    version     = "1.0.0",
                    skill_type  = SkillType.INTEGRATION,
                    source      = SkillSource.MCP,
                    status      = SkillStatus.ACTIVE,
                    category    = "mcp",
                    tags        = ["mcp", "tool"],
                    input_spec  = in_spec,
                    body        = (
                        f"# MCP Tool: {tname}\n\n"
                        f"{tool.get('description', '')}\n\n"
                        "This skill is backed by an MCP server tool."
                    ),
                )
                ok, _ = self._repo.register(spec)
                if ok:
                    self._repo.save_skill(spec)
                    registered += 1
            except Exception as exc:
                log.debug("mcp_tool_conversion_error",
                          extra={"error": str(exc)[:80]})

        log.info("skill_discovery_mcp", extra={"registered": registered})
        return registered

    # ── 3. Remote index scan ─────────────────────────────────────────────────

    def scan_remote_index(self,
                          extra_urls: list[str] | None = None) -> int:
        """
        Pull community skill index endpoints and register any new skills.
        Each URL must return a JSON array.  Supported record shapes:

          {name, description, skill_md_url}   → fetches SKILL.md from url
          {name, description, body}            → uses body directly
          {name, description, ...metadata}     → minimal spec inferred

        Returns the total number of newly registered skills.
        """
        urls = list(_COMMUNITY_INDICES)
        if extra_urls:
            urls += [u for u in extra_urls if u.startswith("https://")]

        if not urls:
            log.debug("skill_discovery_remote_skipped_no_urls")
            return 0

        registered = 0
        for url in urls:
            try:
                registered += self._fetch_and_register_index(url)
            except Exception as exc:
                log.debug("skill_index_fetch_failed",
                          extra={"url": url[:60], "error": str(exc)[:80]})
        log.info("skill_discovery_remote", extra={"registered": registered})
        return registered

    def _fetch_and_register_index(self, url: str) -> int:
        """Fetch one JSON index URL and register its skills."""
        self._ssrf_guard(url)
        try:
            with _req.urlopen(url, timeout=20) as resp:  # noqa: S310
                body = resp.read().decode("utf-8", errors="replace")
            records: list[dict] = _json.loads(body)
        except Exception as exc:
            log.debug("remote_index_parse_failed",
                      extra={"url": url[:60], "error": str(exc)[:80]})
            return 0

        count = 0
        for rec in records:
            if not isinstance(rec, dict):
                continue
            try:
                count += self._register_remote_record(rec)
            except Exception:
                pass
        return count

    def _register_remote_record(self, rec: dict) -> int:
        name = str(rec.get("name", "")).strip()
        if not name:
            return 0

        # If a remote SKILL.md URL is provided, fetch it
        if "skill_md_url" in rec:
            ok, msg = self._repo.import_from_url(
                rec["skill_md_url"], name=name)
            return 1 if ok else 0

        # Otherwise construct a minimal spec from the record
        body = rec.get("body", f"# {name}\n\n{rec.get('description','')}")
        md   = f"---\nname: {name}\ndescription: {rec.get('description','')[:200]}\n---\n\n{body}"
        spec = spec_from_skill_md(
            md_content = md,
            skill_name = name,
            skill_path = "",
            source     = SkillSource.REMOTE,
        )
        ok, _ = self._repo.register(spec)
        if ok:
            self._repo.save_skill(spec)
        return 1 if ok else 0

    # ── 4. Behaviour-pattern mining ──────────────────────────────────────────

    def mine_patterns(self,
                      tool_call_log: list[dict],
                      min_freq: int = 3) -> int:
        """
        Analyse a sequence of logged tool-calls and synthesise new skills for
        frequently co-occurring patterns.

        tool_call_log: list of {"tool": str, "args": dict, "result": str}
        min_freq: minimum times a sequence must appear to be promoted to a skill

        Returns the number of synthesised skills registered.
        """
        if len(tool_call_log) < min_freq:
            return 0

        # Build bigram frequency map
        bigrams: dict[tuple[str, str], int] = {}
        for i in range(len(tool_call_log) - 1):
            a = tool_call_log[i].get("tool", "")
            b = tool_call_log[i + 1].get("tool", "")
            if a and b:
                key = (a, b)
                bigrams[key] = bigrams.get(key, 0) + 1

        registered = 0
        for (tool_a, tool_b), freq in bigrams.items():
            if freq < min_freq:
                continue
            skill_name = f"learned_{tool_a}_then_{tool_b}"
            if self._repo.get(skill_name) is not None:
                continue
            desc = (
                f"Automatically synthesised skill: use {tool_a} followed "
                f"by {tool_b} (observed {freq} times)."
            )
            spec = SkillSpec(
                name        = skill_name,
                description = desc,
                version     = "1.0.0",
                skill_type  = SkillType.AUTOMATION,
                source      = SkillSource.LEARNED,
                status      = SkillStatus.DRAFT,
                category    = "learned",
                tags        = ["learned", "automation", tool_a, tool_b],
                body        = (
                    f"# Learned Pattern: {tool_a} → {tool_b}\n\n"
                    f"{desc}\n\n"
                    f"## Steps\n1. Invoke `{tool_a}`\n2. Invoke `{tool_b}`\n"
                ),
            )
            ok, _ = self._repo.register(spec)
            if ok:
                self._repo.save_skill(spec)
                registered += 1

        log.info("skill_discovery_patterns",
                 extra={"registered": registered, "min_freq": min_freq})
        return registered

    # ── 5. Usage-frequency scoring + progressive prompt surfacing ────────────

    def record_skill_usage(self, skill_name: str) -> None:
        """
        Record that a skill was invoked.  Persists usage counters to
        workspace/skills/.usage.json so rankings survive restarts.
        """
        with self._lock:
            self._usage_counts[skill_name] = \
                self._usage_counts.get(skill_name, 0) + 1
            self._usage_last[skill_name]   = _time.time()
            self._save_usage()

    def _load_usage(self) -> None:
        """Load persisted usage counters from disk (called in __init__)."""
        path = self._workspace / "skills" / ".usage.json"
        if path.exists():
            try:
                raw = _json.loads(path.read_text(encoding="utf-8"))
                self._usage_counts = raw.get("counts", {})
                self._usage_last   = raw.get("last", {})
            except Exception:
                pass

    def _save_usage(self) -> None:
        path = self._workspace / "skills" / ".usage.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _json.dumps({"counts": self._usage_counts,
                             "last":   self._usage_last}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def skill_score(self, skill_name: str) -> float:
        """
        Composite relevance score for progressive prompt surfacing.

          use_count × 1.5
          + recency_bonus (0–5 based on time since last use, 7d window)
          + install_bonus (1.0 if locally installed)
        """
        count   = self._usage_counts.get(skill_name, 0)
        last    = self._usage_last.get(skill_name, 0.0)
        age_s   = _time.time() - last if last else 604800
        recency = max(0.0, 5.0 * (1.0 - age_s / 604800))
        install = 1.0 if (self._workspace / "skills" / skill_name / "SKILL.md").exists() else 0.0
        return count * 1.5 + recency + install

    def top_skills(self, limit: int = 8,
                   context_keywords: list[str] | None = None) -> list[str]:
        """
        Return the top `limit` skill names ranked by usage score.
        If context_keywords are provided, boost skills whose name or tags
        contain a keyword.
        """
        all_names = list(self._repo.all_names()) \
            if hasattr(self._repo, "all_names") else list(self._usage_counts)

        def _boost(name: str) -> float:
            base = self.skill_score(name)
            if context_keywords:
                for kw in context_keywords:
                    if kw.lower() in name.lower():
                        base += 2.0
            return base

        ranked = sorted(all_names, key=_boost, reverse=True)
        return ranked[:limit]

    def progressive_prompts(self,
                            limit: int = 6,
                            context: str = "") -> list[dict]:
        """
        Surface prompts for the top-ranked skills based on usage history.
        Returns a list of {"skill": str, "prompt": str, "score": float}
        dicts, ready for the UI's quick-prompt strip.
        """
        kws = _re.split(r"\W+", context.lower()) if context else []
        names = self.top_skills(limit=limit * 2, context_keywords=kws or None)
        results = []
        for name in names:
            spec = self._repo.get(name) if hasattr(self._repo, "get") else None
            if spec is None:
                continue
            desc = getattr(spec, "description", "") or name
            prompt = f"Use the {name} skill to: {desc[:80]}"
            results.append({
                "skill": name,
                "prompt": prompt,
                "score":  round(self.skill_score(name), 3),
            })
            if len(results) >= limit:
                break
        return results

    # ── Convenience: scan all sources ────────────────────────────────────────

    def scan_all(self,
                 mcp_clients: list[Any] | None = None,
                 extra_urls:  list[str] | None = None) -> dict[str, int]:
        """Run all discovery passes and return per-source counts."""
        local  = self.scan_local()
        mcp    = 0
        for client in (mcp_clients or []):
            mcp += self.scan_mcp(client)
        remote = self.scan_remote_index(extra_urls=extra_urls)
        return {"local": local, "mcp": mcp, "remote": remote}

    # ── SSRF guard (shared) ───────────────────────────────────────────────────

    @staticmethod
    def _ssrf_guard(url: str) -> None:
        """Raise ValueError if the URL resolves to a private/loopback address."""
        import ipaddress as _ipa
        import socket    as _sock

        parsed   = _urlparse.urlparse(url)
        scheme   = parsed.scheme.lower()
        hostname = parsed.hostname or ""

        if scheme != "https":
            raise ValueError(f"Non-HTTPS URL rejected: {url[:60]}")
        if not hostname:
            raise ValueError("URL has no resolvable hostname.")

        for _, _, _, _, sockaddr in _sock.getaddrinfo(hostname, None):
            ip = _ipa.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError(
                    f"SSRF guard: '{hostname}' resolves to {ip} (private/reserved)."
                )
