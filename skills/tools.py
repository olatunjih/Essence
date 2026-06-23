"""
Skill system agent tools — registered into TOOL_REGISTRY so the LLM can:

  skill_search   — find relevant skills by free-text query
  skill_execute  — run a named skill with JSON input
  skill_create   — create a new skill (name + SKILL.md content)
  skill_list     — list all registered skills with metadata
  skill_promote  — promote a DRAFT skill to ACTIVE
  skill_build    — trigger autonomous gap-detection + synthesis

All tools are stateless closures that capture the SkillSystem at
registration time.  Registration happens in boot.py immediately after
the SkillSystem is constructed.
"""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
import json as _json
from typing import Any

log = logging.getLogger("essence.skills.tools")

# ══════════════════════════════════════════════════════════════════════════════
# Tool schemas (OpenAI function-calling format)
# ══════════════════════════════════════════════════════════════════════════════

SKILL_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "skill_search",
            "description": (
                "Search the skill repository for relevant skills using a "
                "free-text query.  Returns a ranked list of matching skills "
                "with name, description, category, and status."
            ),
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Free-text description of the capability you need."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return (default 10).",
                        "default": 10
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter (e.g. 'analysis', 'retrieval')."
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_execute",
            "description": (
                "Execute a named skill from the repository.  Validates inputs, "
                "runs the skill body through the router, and returns the result."
            ),
            "parameters": {
                "type": "object",
                "required": ["skill_name"],
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "The registered name of the skill to execute."
                    },
                    "input": {
                        "type": "object",
                        "description": "Input parameters as a JSON object.",
                        "additionalProperties": True,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_create",
            "description": (
                "Create and register a new skill.  Provide either a complete "
                "SKILL.md string (with YAML frontmatter) or a name + description "
                "to generate a template automatically."
            ),
            "parameters": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Unique skill name (alphanumeric + hyphens/underscores)."
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line description of what the skill does."
                    },
                    "skill_md": {
                        "type": "string",
                        "description": (
                            "Full SKILL.md content including YAML frontmatter. "
                            "If omitted, a template is generated from name + description."
                        )
                    },
                    "category": {
                        "type": "string",
                        "description": "Category tag (e.g. 'analysis', 'retrieval', 'automation').",
                        "default": "general"
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": (
                "List all skills in the repository.  Returns name, description, "
                "category, status, source, version, and usage stats."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status: 'active', 'draft', 'deprecated', or 'all'.",
                        "default": "active"
                    },
                    "source": {
                        "type": "string",
                        "description": "Filter by source: 'local', 'remote', 'mcp', 'learned', or 'all'."
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_promote",
            "description": (
                "Promote a DRAFT skill to ACTIVE status, making it available "
                "for execution and injection into tool-call payloads."
            ),
            "parameters": {
                "type": "object",
                "required": ["skill_name"],
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the DRAFT skill to promote."
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_build",
            "description": (
                "Trigger autonomous gap detection and skill synthesis.  "
                "Analyses recent failures and capability requirements to "
                "generate new DRAFT skills that fill identified gaps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "capability_matrix": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of required capability descriptions.  "
                            "Skills are synthesised for any not already covered."
                        )
                    },
                },
            },
        },
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# Handler factories (closures over skill_system)
# ══════════════════════════════════════════════════════════════════════════════

def _make_skill_search(skill_system: Any):
    def _handler(args: dict) -> str:
        query    = str(args.get("query", "")).strip()
        limit    = int(args.get("limit", 10))
        category = args.get("category") or None
        if not query:
            return "[skill_search error: query is required]"
        results = skill_system.repository.search(
            query    = query,
            category = category,
            limit    = limit,
        )
        if not results:
            return f"No skills found matching '{query}'."
        lines = [f"Skills matching '{query}':"]
        for s in results:
            lines.append(
                f"  • {s.name} [{s.status.value}] ({s.category}): "
                f"{s.description[:80]}"
            )
        return "\n".join(lines)
    return _handler


def _make_skill_execute(skill_system: Any):
    def _handler(args: dict) -> str:
        name       = str(args.get("skill_name", "")).strip()
        input_data = args.get("input") or {}
        if not name:
            return "[skill_execute error: skill_name is required]"
        if isinstance(input_data, str):
            try:
                input_data = _json.loads(input_data)
            except Exception:
                input_data = {"input": input_data}
        result = skill_system.executor.execute(name, input_data)
        if result.ok:
            return result.result
        return f"[skill_execute error ({result.status})]: {result.error}"
    return _handler


def _make_skill_create(skill_system: Any):
    def _handler(args: dict) -> str:
        name      = str(args.get("name", "")).strip()
        desc      = str(args.get("description", "")).strip()
        skill_md  = args.get("skill_md") or ""
        category  = str(args.get("category", "general")).strip()

        if not name:
            return "[skill_create error: name is required]"

        if not skill_md:
            # Build a minimal SKILL.md template
            skill_md = (
                f"---\nname: {name}\nversion: '1.0.0'\n"
                f"description: {desc or 'No description provided.'}\n"
                f"skill_type: general\ncategory: {category}\n"
                f"guardrails:\n  max_execution_time_seconds: 120\n"
                f"  max_tokens: 2048\n---\n\n"
                f"# {name}\n\n{desc or 'Describe this skill here.'}\n\n"
                f"## Instructions\n\nAdd step-by-step instructions here.\n"
            )

        from essence.skills.models import spec_from_skill_md, SkillSource
        spec = spec_from_skill_md(
            md_content = skill_md,
            skill_name = name,
            skill_path = "",
            source     = SkillSource.LOCAL,
        )
        ok, msg = skill_system.repository.register(spec, force=False)
        if ok:
            skill_system.repository.save_skill(spec)
        return msg
    return _handler


def _make_skill_list(skill_system: Any):
    def _handler(args: dict) -> str:
        status_filter = str(args.get("status", "active")).lower()
        source_filter = str(args.get("source", "all")).lower()

        from essence.skills.models import SkillStatus, SkillSource

        all_skills = skill_system.repository.all()

        if status_filter != "all":
            try:
                sf = SkillStatus(status_filter)
                all_skills = [s for s in all_skills if s.status == sf]
            except ValueError:
                pass

        if source_filter != "all":
            try:
                src = SkillSource(source_filter)
                all_skills = [s for s in all_skills if s.source == src]
            except ValueError:
                pass

        if not all_skills:
            return "No skills match the specified filters."

        lines = [f"Skills ({len(all_skills)} total):"]
        for s in sorted(all_skills, key=lambda x: x.name):
            lines.append(
                f"  [{s.status.value}] {s.name} v{s.version} "
                f"({s.source.value}/{s.category}): {s.description[:60]}"
                f"  uses={s.use_count} err={s.error_rate:.2f}"
            )
        return "\n".join(lines)
    return _handler


def _make_skill_promote(skill_system: Any):
    def _handler(args: dict) -> str:
        name = str(args.get("skill_name", "")).strip()
        if not name:
            return "[skill_promote error: skill_name is required]"
        ok, msg = skill_system.builder.promote(name)
        return msg
    return _handler


def _make_skill_build(skill_system: Any):
    def _handler(args: dict) -> str:
        capability_matrix = args.get("capability_matrix") or []
        if isinstance(capability_matrix, str):
            capability_matrix = [capability_matrix]
        specs = skill_system.builder.synthesise_all_gaps(
            capability_matrix=capability_matrix or None
        )
        if not specs:
            return "No capability gaps detected — skill set is complete."
        lines = [f"Synthesised {len(specs)} new DRAFT skills:"]
        for s in specs:
            lines.append(f"  • {s.name}: {s.description[:80]}")
        lines.append("\nUse skill_promote to activate each skill after review.")
        return "\n".join(lines)
    return _handler


# ══════════════════════════════════════════════════════════════════════════════
# Registration helper — called from boot.py
# ══════════════════════════════════════════════════════════════════════════════

def register_skill_tools(registry: Any, skill_system: Any) -> None:
    """
    Register all 6 skill agent tools into the provided ToolRegistry.
    Called once after both SkillSystem and ToolRegistry are ready.
    """
    handler_factories = {
        "skill_search":  _make_skill_search,
        "skill_execute": _make_skill_execute,
        "skill_create":  _make_skill_create,
        "skill_list":    _make_skill_list,
        "skill_promote": _make_skill_promote,
        "skill_build":   _make_skill_build,
    }
    for schema in SKILL_TOOL_SCHEMAS:
        tool_name = schema["function"]["name"]
        factory   = handler_factories.get(tool_name)
        if factory is None:
            continue
        try:
            registry.register(schema, factory(skill_system))
            log.debug("skill_tool_registered", extra={"tool": tool_name})
        except Exception as exc:
            log.warning("skill_tool_registration_failed",
                        extra={"tool": tool_name, "error": str(exc)[:80]})
    log.info("skill_tools_registered",
             extra={"count": len(SKILL_TOOL_SCHEMAS)})
