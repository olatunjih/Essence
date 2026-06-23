"""Typed data models for the Essence skill system."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
import dataclasses as _dc
import enum as _enum
import json as _json
import re as _re
import time as _time
from typing import Any, Callable

log = logging.getLogger("essence.skills.models")

# ══════════════════════════════════════════════════════════════════════════════
# Enumerations
# ══════════════════════════════════════════════════════════════════════════════

class SkillType(_enum.Enum):
    """Broad functional category of a skill (mirrors A2A / MCP conventions)."""
    ANALYSIS    = "analysis"
    GENERATION  = "generation"
    RETRIEVAL   = "retrieval"
    REASONING   = "reasoning"
    AUTOMATION  = "automation"
    INTEGRATION = "integration"
    MEMORY      = "memory"
    COMPOSITION = "composition"
    GENERAL     = "general"


class SkillSource(_enum.Enum):
    LOCAL     = "local"       # workspace/skills/ dir
    REMOTE    = "remote"      # imported from HTTPS URL
    MCP       = "mcp"         # discovered via MCP server tool-list
    LEARNED   = "learned"     # autonomously synthesised from patterns
    BUILT_IN  = "built_in"   # hardcoded in Python source


class SkillStatus(_enum.Enum):
    ACTIVE     = "active"
    DEPRECATED = "deprecated"
    DRAFT      = "draft"
    DISABLED   = "disabled"


# ══════════════════════════════════════════════════════════════════════════════
# Core typed models
# ══════════════════════════════════════════════════════════════════════════════

@_dc.dataclass
class SkillInputSpec:
    """JSON Schema fragment for skill inputs."""
    properties: dict[str, dict] = _dc.field(default_factory=dict)
    required:   list[str]       = _dc.field(default_factory=list)
    description: str            = ""

    def to_schema(self) -> dict:
        s: dict = {"type": "object", "properties": self.properties}
        if self.required:
            s["required"] = self.required
        if self.description:
            s["description"] = self.description
        return s


@_dc.dataclass
class SkillOutputSpec:
    """JSON Schema fragment for skill outputs."""
    properties: dict[str, dict] = _dc.field(default_factory=dict)
    description: str            = ""

    def to_schema(self) -> dict:
        s: dict = {"type": "object", "properties": self.properties}
        if self.description:
            s["description"] = self.description
        return s


@_dc.dataclass
class SkillGuardrails:
    max_execution_time_seconds: float = 120.0
    max_tokens:                 int   = 2048
    max_retries:                int   = 2
    require_confirmation:       bool  = False
    log_inputs:                 bool  = True
    log_outputs:                bool  = True


@_dc.dataclass
class SkillSpec:
    """
    Full specification for a skill — the canonical model used by the
    SkillRepository, SkillExecutor, and SkillDiscovery subsystems.

    Serialisable to/from the YAML frontmatter of a SKILL.md file.
    """
    name:           str
    description:    str
    version:        str             = "1.0.0"
    skill_type:     SkillType       = SkillType.GENERAL
    source:         SkillSource     = SkillSource.LOCAL
    status:         SkillStatus     = SkillStatus.ACTIVE
    category:       str             = "general"
    tags:           list[str]       = _dc.field(default_factory=list)
    author:         str             = ""
    homepage:       str             = ""
    input_spec:     SkillInputSpec  = _dc.field(default_factory=SkillInputSpec)
    output_spec:    SkillOutputSpec = _dc.field(default_factory=SkillOutputSpec)
    guardrails:     SkillGuardrails = _dc.field(default_factory=SkillGuardrails)
    dependencies:   list[str]       = _dc.field(default_factory=list)
    examples:       list[dict]      = _dc.field(default_factory=list)
    body:           str             = ""
    skill_path:     str             = ""
    registered_at:  float           = _dc.field(default_factory=_time.time)
    last_used_at:   float           = 0.0
    use_count:      int             = 0
    avg_latency_ms: float           = 0.0
    error_rate:     float           = 0.0
    # A2A / MCP exposition flags
    expose_via_a2a: bool            = False
    expose_via_mcp: bool            = False

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        d = _dc.asdict(self)
        d["skill_type"] = self.skill_type.value
        d["source"]     = self.source.value
        d["status"]     = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SkillSpec":
        d = dict(d)
        d["skill_type"] = SkillType(d.get("skill_type", "general"))
        d["source"]     = SkillSource(d.get("source", "local"))
        d["status"]     = SkillStatus(d.get("status", "active"))
        for key, klass in [
            ("input_spec",  SkillInputSpec),
            ("output_spec", SkillOutputSpec),
            ("guardrails",  SkillGuardrails),
        ]:
            if isinstance(d.get(key), dict):
                try:
                    d[key] = klass(**{k: v for k, v in d[key].items()
                                      if k in klass.__dataclass_fields__})
                except Exception:
                    d[key] = klass()
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})

    def to_skill_md(self) -> str:
        """Render this spec as a SKILL.md file (YAML frontmatter + markdown body)."""
        try:
            import yaml as _yaml
            front = _yaml.dump({
                "name":        self.name,
                "version":     self.version,
                "description": self.description,
                "skill_type":  self.skill_type.value,
                "category":    self.category,
                "tags":        self.tags,
                "author":      self.author,
                "homepage":    self.homepage,
                "input_schema":  self.input_spec.to_schema(),
                "output_schema": self.output_spec.to_schema(),
                "guardrails": {
                    "max_execution_time_seconds": self.guardrails.max_execution_time_seconds,
                    "max_tokens":                 self.guardrails.max_tokens,
                    "max_retries":                self.guardrails.max_retries,
                    "require_confirmation":        self.guardrails.require_confirmation,
                },
                "observability": {
                    "log_inputs":  self.guardrails.log_inputs,
                    "log_outputs": self.guardrails.log_outputs,
                },
                "a2a": {"expose": self.expose_via_a2a},
                "mcp": {"expose": self.expose_via_mcp},
                "dependencies": self.dependencies,
            }, sort_keys=False, allow_unicode=True)
        except ImportError:
            front = (
                f"name: {self.name}\n"
                f"version: {self.version}\n"
                f"description: {self.description}\n"
                f"skill_type: {self.skill_type.value}\n"
                f"category: {self.category}\n"
            )
        body = self.body or f"# {self.name}\n\n{self.description}\n"
        return f"---\n{front}---\n\n{body}"

    def matches_query(self, query: str) -> float:
        """
        Simple relevance score in [0, 1] between this skill and a free-text query.
        Higher → more relevant.  Used by SkillRepository.search().
        """
        q = query.lower()
        score = 0.0
        targets = [
            (self.name.lower(),        0.35),
            (self.description.lower(), 0.30),
            (self.category.lower(),    0.15),
            (" ".join(self.tags).lower(), 0.20),
        ]
        for text, weight in targets:
            if q in text:
                score += weight
            else:
                words = q.split()
                hits  = sum(1 for w in words if w in text)
                score += weight * (hits / max(len(words), 1)) * 0.5
        return min(score, 1.0)


# ══════════════════════════════════════════════════════════════════════════════
# Execution result
# ══════════════════════════════════════════════════════════════════════════════

@_dc.dataclass
class SkillResult:
    skill_name:        str
    status:            str   # "success" | "error" | "timeout" | "validation_error"
    result:            str   = ""
    error:             str   = ""
    elapsed_ms:        float = 0.0
    validation_passed: bool  = True
    retry_count:       int   = 0
    used_router:       bool  = False

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict:
        return _dc.asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# YAML frontmatter parser (shared, tolerant)
# ══════════════════════════════════════════════════════════════════════════════

def parse_skill_frontmatter(md_content: str) -> tuple[dict, str]:
    """
    Parse YAML frontmatter from a Markdown string.
    Returns (frontmatter_dict, body_without_frontmatter).
    Returns ({}, original_content) when no valid frontmatter is found.
    """
    stripped = md_content.lstrip()
    if not stripped.startswith("---"):
        return {}, md_content

    lines   = stripped.splitlines()
    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, md_content

    yaml_block = "\n".join(lines[1:end_idx])
    body       = "\n".join(lines[end_idx + 1:])

    try:
        import yaml as _yaml
        data = _yaml.safe_load(yaml_block) or {}
        return data, body
    except Exception:
        data = {}
        for ln in yaml_block.splitlines():
            if ":" in ln:
                k, _, v = ln.partition(":")
                data[k.strip()] = v.strip()
        return data, body


def spec_from_skill_md(md_content: str,
                        skill_name: str,
                        skill_path: str = "",
                        source: SkillSource = SkillSource.LOCAL) -> SkillSpec:
    """
    Parse a SKILL.md string into a SkillSpec.
    Fills reasonable defaults when frontmatter fields are missing.
    """
    front, body = parse_skill_frontmatter(md_content)

    # ── Input / output schemas ────────────────────────────────────────────────
    in_raw  = front.get("input_schema",  {}) or {}
    out_raw = front.get("output_schema", {}) or {}
    in_spec  = SkillInputSpec(
        properties  = in_raw.get("properties", {}),
        required    = in_raw.get("required",   []),
        description = in_raw.get("description",""),
    )
    out_spec = SkillOutputSpec(
        properties  = out_raw.get("properties", {}),
        description = out_raw.get("description",""),
    )

    # ── Guardrails ────────────────────────────────────────────────────────────
    gr_raw = front.get("guardrails", {}) or {}
    obs_raw = front.get("observability", {}) or {}
    guardrails = SkillGuardrails(
        max_execution_time_seconds = float(gr_raw.get("max_execution_time_seconds", 120)),
        max_tokens                 = int(gr_raw.get("max_tokens", 2048)),
        max_retries                = int(gr_raw.get("max_retries", 2)),
        require_confirmation       = bool(gr_raw.get("require_confirmation", False)),
        log_inputs                 = bool(obs_raw.get("log_inputs",  True)),
        log_outputs                = bool(obs_raw.get("log_outputs", True)),
    )

    # ── A2A / MCP exposition ──────────────────────────────────────────────────
    a2a_raw = front.get("a2a", {}) or {}
    mcp_raw = front.get("mcp", {}) or {}

    # ── Canonical description ─────────────────────────────────────────────────
    desc = str(front.get("description", "")).strip()
    if not desc:
        for ln in body.splitlines():
            if ln.strip() and not ln.strip().startswith(("#", "---")):
                desc = ln.strip()[:120]
                break

    # ── Skill type ────────────────────────────────────────────────────────────
    try:
        stype = SkillType(front.get("skill_type", "general"))
    except ValueError:
        stype = SkillType.GENERAL

    return SkillSpec(
        name           = str(front.get("name", skill_name)),
        description    = desc,
        version        = str(front.get("version", "1.0.0")),
        skill_type     = stype,
        source         = source,
        status         = SkillStatus.ACTIVE,
        category       = str(front.get("category", "general")),
        tags           = list(front.get("tags", []) or []),
        author         = str(front.get("author", "") or ""),
        homepage       = str(front.get("homepage", "") or ""),
        input_spec     = in_spec,
        output_spec    = out_spec,
        guardrails     = guardrails,
        dependencies   = list(front.get("dependencies", []) or []),
        examples       = list(front.get("examples", []) or []),
        body           = body,
        skill_path     = skill_path,
        expose_via_a2a = bool(a2a_raw.get("expose", False)),
        expose_via_mcp = bool(mcp_raw.get("expose", False)),
    )
