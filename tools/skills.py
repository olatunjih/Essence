"""skill composition +  bootstrap + use_skill tool."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.tools.registry import BUILTIN_TOOLS, TOOL_REGISTRY  # noqa: F401  [real source bug: used at module-level bootstrap below without this import]

# SKILL COMPOSITION
# ══════════════════════════════════════════════════════════════════════════════
# Skills can now invoke other skills at runtime via the `use_skill` tool.
# A code_review skill can call web_search to fetch documentation; a research
# skill can call summarize; etc.
#
# Cycle detection: a per-invocation call stack tracks which skills are active.
# If skill A calls skill B which tries to call skill A, use_skill raises
# SkillCycleError immediately rather than stack-overflowing.
#
# Usage (from within a skill or agent tool-call):
#   result = _tool_use_skill({"skill_name": "web_search",
#                              "task": "find docs for pathlib.rglob"})

class SkillCycleError(RuntimeError):
    pass

# Thread-local call stack for cycle detection
_skill_call_stack = threading.local()


def _tool_use_skill(args: dict, *, _registry: Any = None) -> str:
    """
    Invoke a named skill from within another skill or agent step.

    args:
      skill_name (str): Name of the skill to invoke.
      task       (str): The task/prompt to send to that skill.

    Cycle detection: raises SkillCycleError if the target skill is already
    active in the current call chain.
    """
    skill_name = str(args.get("skill_name", "")).strip()
    task       = str(args.get("task", "")).strip()

    if not skill_name:
        return "[use_skill error: skill_name is required]"
    if not task:
        return "[use_skill error: task is required]"

    # ── Cycle detection ──────────────────────────────────────────────────────
    stack: list[str] = getattr(_skill_call_stack, "stack", [])
    if skill_name in stack:
        cycle_path = " → ".join(stack + [skill_name])
        log.warning("skill_cycle_detected", extra={"cycle": cycle_path})
        return f"[use_skill error: cycle detected ({cycle_path})]"

    # ── Resolve skill handler via registry or TOOL_REGISTRY ─────────────────
    reg = _registry or (TOOL_REGISTRY if "TOOL_REGISTRY" in globals() else None)
    if reg is None:
        return "[use_skill error: TOOL_REGISTRY not available yet]"

    handler = reg.get_handler(skill_name)
    if handler is None:
        return f"[use_skill error: skill '{skill_name}' not found in registry]"

    # ── Push stack, invoke, pop stack ────────────────────────────────────────
    if not hasattr(_skill_call_stack, "stack"):
        _skill_call_stack.stack = []
    _skill_call_stack.stack.append(skill_name)
    try:
        result = handler({"task": task, "prompt": task})
        return str(result)
    except SkillCycleError:
        raise
    except Exception as _e:
        log.warning("use_skill_invocation_error",
                    extra={"skill": skill_name, "error": str(_e)[:200]})
        return f"[use_skill error invoking '{skill_name}': {_e}]"
    finally:
        _skill_call_stack.stack.pop()


_USE_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": (
            "Invoke a named skill from within another skill or agent step. "
            "Allows skills to compose — e.g. code_review can call web_search "
            "to fetch documentation. Cycle detection prevents infinite recursion."
        ),
        "parameters": {
            "type": "object",
            "required": ["skill_name", "task"],
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill/tool to invoke."
                },
                "task": {
                    "type": "string",
                    "description": "The task or prompt to pass to the target skill."
                },
            },
        },
    },
}



# TOOL_REGISTRY BOOTSTRAP
# ══════════════════════════════════════════════════════════════════════════════
# This runs once at module load time.  The Agent reads TOOL_REGISTRY.get_tools()
# instead of the raw BUILTIN_TOOLS list so that dynamically registered tools
# are automatically included in every LLM tool-call payload.
#
# NOTE: handlers that need workspace/memory/hw context are registered as
# closures inside Agent.__init__ via TOOL_REGISTRY.register() so they capture
# the right scope.  The stubs below register the *schemas* now; handlers for
# context-sensitive tools are upserted at Agent construction time.

def _bootstrap_tool_registry() -> None:
    """Pre-register all builtin tool schemas into TOOL_REGISTRY.
    Handlers for stateless tools are wired here.
    Context-sensitive tool handlers (need ws/hw/mem) are registered per-Agent
    instance via Agent._register_tool_handlers()."""
    for schema in BUILTIN_TOOLS:
        name = schema["function"]["name"]
        # Register schema now; handler placeholder — Agent overwrites per instance
        if not TOOL_REGISTRY.get_handler(name):
            TOOL_REGISTRY._schemas.append(schema)

_bootstrap_tool_registry()

# Register use_skill tool (v20 — skill composition with cycle detection)
TOOL_REGISTRY.register(_USE_SKILL_SCHEMA, _tool_use_skill)
log.debug("use_skill_registered")

# Register MCP memory tools — enabled when team_id set or Essence_MCP_MEMORY=1
if _TEAM_ID != "local" or os.environ.get("Essence_MCP_MEMORY", "0") == "1":
    register_mcp_memory_tools(TOOL_REGISTRY)
    log.debug("mcp_memory_auto_registered", extra={"team_id": _TEAM_ID})


# ══════════════════════════════════════════════════════════════════════════════
