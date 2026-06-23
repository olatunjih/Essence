"""scaffold +  default workspace files +  workspace helpers."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.server.web_ui import (  # noqa: F401
    _web_ui_html, _server_app_src, _workspace_main_src, _TUI_SRC,
)


def _new_file(path: Path, content: str) -> bool:
    """Write `content` to `path` only if the file does not already exist.
    Returns True if the file was created, False if it already existed."""
    if path.exists():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    except Exception:
        return False


def _config_toml(hw: "HardwareProfile", ws: Path) -> str:
    """Generate the initial config.toml for this workspace / hardware profile."""
    return textwrap.dedent(f"""\
        # Essence workspace configuration
        # Generated automatically — edit to override defaults.

        [model]
        backend  = "{hw.backend}"
        model    = "{hw.model}"
        tier     = {hw.tier}

        [server]
        host     = "0.0.0.0"
        port     = 7860

        [memory]
        backend  = "json"   # json | faiss | sqlite-vec | chromadb | qdrant

        [security]
        autonomy_tier  = 2   # 0-confirm-all  1-confirm-destructive  2-auto  3-fully-auto
        vault_allow_weak = false

        [workspace]
        path     = "{ws}"
    """)

# WORKSPACE  (workspace-compatible file structure)
# ══════════════════════════════════════════════════════════════════════════════
# Full file roster matches Essence's workspace kernel:
#   SOUL.md       — agent identity, personality, values (who the agent IS)
#   HEARTBEAT.md  — recurring task instructions (what it watches for)
#   AGENTS.md     — agent roster, routing rules, trust levels
#   IDENTITY.md   — user identity, preferences, context
#   TOOLS.md      — tool whitelist + domain policies (feeds CriticGate constraints)
#   MEMORY.md     — distilled long-term memory (human-readable)
#   skills/       — skill-compatible SKILL.md directories
#   sessions/     — JSONL session transcripts (append-only per Essence pattern)
#   memory/       — structured memory (JSON + optional ChromaDB)
#   logs/         — heartbeat + tool execution logs

def _workspace_root() -> Path:
    """Delegates to the canonical workspace_root() in essence.core.paths."""
    try:
        from essence.core.paths import workspace_root
        return workspace_root()
    except ImportError:
        if env := os.environ.get("Essence_WORKSPACE", ""):
            return Path(env)
        return Path.home() / ".essence"


def load_ws_file(workspace: Path, filename: str,
                 default: str = "") -> str:
    p = workspace / filename
    try:
        return p.read_text(encoding="utf-8") if p.exists() else default
    except Exception:
        return default


# ══════════════════════════════════════════════════════════════════════════════

# DEFAULT WORKSPACE FILES
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_SOUL = textwrap.dedent("""\
    # SOUL.md

    ## Identity
    You are Essence — a local-first personal AI agent.
    You run on this machine and exist to serve your user, not a corporation.

    ## Personality
    Be the assistant you'd actually want at 2am. Direct. Capable. Honest.
    Not a sycophant. Not a corporate drone. Not performatively helpful.
    If you don't know something, say so. If a task is risky, say so.

    ## Values
    - **Privacy first**: Data never leaves this machine unless the user explicitly asks.
    - **Transparency**: Explain what you're doing and why. No hidden actions.
    - **Safety**: Never execute irreversible operations without explicit confirmation.
    - **Efficiency**: Do the task. Don't pad responses. Don't over-explain.

    ## Work style
    - For concrete tasks, prefer shell tools over long prose answers.
    - For multi-step tasks, show a brief plan before acting.
    - When using web search, cite your sources.
    - Always verify destructive operations before running them.
""").strip()

_DEFAULT_HEARTBEAT = textwrap.dedent("""\
    # HEARTBEAT.md
    # This file is read on every heartbeat tick (default every 30 minutes).
    # If this file is empty, the agent replies HEARTBEAT_OK and skips delivery.
    # Add tasks below to activate autonomous background operation.

    ## Active tasks
    <!-- Uncomment or add tasks:

    - Check if any files in ~/Downloads are older than 7 days and remind me.
    - Summarize the top 3 AI news headlines since last heartbeat.
    - Monitor system disk usage; alert if > 85%.

    -->
""").strip()

_DEFAULT_AGENTS = textwrap.dedent("""\
    # AGENTS.md
    # Defines available agent configurations and routing rules.

    ## Default Agent
    - soul: SOUL.md
    - model: auto (set by hardware probe)
    - thinking: false
    - heartbeat: HEARTBEAT.md

    ## Routing
    # Messages prefixed with "/agent:" route to named agents.
    # Example: /agent:coder Please refactor main.py
""").strip()

_DEFAULT_IDENTITY = textwrap.dedent("""\
    # IDENTITY.md
    # Fill this in to give your agent persistent knowledge about you.

    ## About me
    - Name:
    - Timezone:
    - Preferred language:
    - Current projects:
    - Tools I use daily:

    ## Preferences
    - Response length: concise unless detail is needed
    - Code style: (e.g. Python PEP8, no unnecessary comments)
    - Communication: direct and honest
""").strip()

# ── Structured identity files (PAI-inspired: goals / projects / learned) ─────
_DEFAULT_GOALS = textwrap.dedent("""\
    # GOALS.md
    # What you are working toward. The agent reads this every turn and surfaces
    # relevant context proactively. Edit freely — the agent will not overwrite this.

    ## Long-term goals
    <!-- e.g. Ship Essence v1.0 as an installable pip package -->

    ## Short-term goals (this week)
    <!-- e.g. Fix hybrid memory, add MCP server endpoint -->

    ## Anti-goals (things deliberately NOT being pursued)
    <!-- e.g. No cloud-only dependency, no mandatory login -->
""").strip()

_DEFAULT_PROJECTS = textwrap.dedent("""\
    # PROJECTS.md
    # Active projects. The proactive engine uses this to surface stale work.
    # Format each entry as a YAML block so the engine can parse last_updated.

    ## Projects

    # - name: Example Project
    #   status: active          # active | paused | done
    #   last_updated: 2026-01-01
    #   description: One-line description
    #   next_step: What to do next
""").strip()

_DEFAULT_LEARNED = textwrap.dedent("""\
    # LEARNED.md
    # Persistent lessons and discoveries. The agent appends here after eval runs,
    # significant task completions, and error recovery.  You can also write here.
    # Entries are injected into the system prompt as long-term context.

    ## Technical insights
    <!-- Agent-written discoveries appear here -->

    ## Mistakes to avoid
    <!-- Agent-written error patterns appear here -->

    ## User preferences learned
    <!-- Agent-inferred preferences appear here -->
""").strip()

_DEFAULT_TOOLS = textwrap.dedent("""\
    # TOOLS.md
    # Domain policies for CriticGate constraint synthesis.
    # Each rule becomes a guarded constraint during step validation.

    ## Constraints
    - NEVER delete files without explicit user confirmation.
    - NEVER send data to external URLs unless the user has approved the target.
    - NEVER execute commands that modify system configuration (sudo, chown root, etc.).
    - ALWAYS show a plan before running more than 3 sequential shell commands.
    - web_search: allowed at any time.
    - write_file: allowed within workspace only (allow_outside = false by default).
    - python_exec: sandboxed subprocess only.
""").strip()

_DEFAULT_MEMORY = textwrap.dedent("""\
    # MEMORY.md
    # Long-term distilled memory. Updated automatically by the agent.
    # You can also edit this manually — the agent reads it on every turn.

    ## Key facts
    <!-- Agent-written facts appear here -->

    ## Ongoing projects
    <!-- Agent-tracked project context appears here -->
""").strip()

_EXAMPLE_SKILL = textwrap.dedent("""\
    ---
    name: morning-briefing
    description: Delivers a personalised morning briefing: headlines, calendar, tasks.
    tools: [web_search, read_file]
    trigger: manual or heartbeat "morning"
    ---

    # Morning Briefing Skill

    When this skill is activated:

    1. web_search "top AI and tech news today" — extract top 3 headlines.
    2. read_file "tasks.md" in the workspace — list today's open tasks (if the file exists).
    3. Compile a brief briefing (under 250 words):
       - 3 headlines with one-sentence summaries
       - Today's tasks
       - One short motivational or interesting fact
    4. Deliver the briefing conversationally, not as a formal report.
""").strip()


# ══════════════════════════════════════════════════════════════════════════════

# SCAFFOLD
# ══════════════════════════════════════════════════════════════════════════════
# Writes every workspace file from inline string templates.
# NEVER overwrites existing user-edited files (_new_file guard).
# Re-running scaffold is always safe — idempotent.
#
# On first run: also auto-scaffolds installable sub-packages under
# ~/.essence/packages/ so `pip install -e ~/.essence/packages/essence-cli` works
# immediately without a separate `install-packages` invocation.
# Sub-packages are stubs that import from this monolith; extract incrementally.

def scaffold(ws: Path, hw: "HardwareProfile") -> None:
    """
    Write the full Essence workspace tree under `ws`.

    On very first run also auto-scaffolds the installable sub-packages under
    ~/.essence/packages/ so `pip install -e ~/.essence/packages/essence-cli` works
    immediately without a separate `install-packages` invocation.
    Sub-packages are stubs that import from this monolith — you can extract
    code into them incrementally without breaking anything.
    """
    for d in [ws/"server", ws/"memory", ws/"sessions", ws/"logs",
              ws/"skills"/"morning-briefing", ws/"models",
              ws/"plots", ws/"experiments", ws/"runs"]:
        d.mkdir(parents=True, exist_ok=True)

    # ── First-run: auto-scaffold installable packages ────────────────────────
    # install_packages() is defined in  (below scaffold) — Python resolves
    # this at call-time, so the forward reference works correctly at runtime.
    pkg_root = ws / "packages"
    if not pkg_root.exists():
        log.info("scaffold_packages",
                 extra={"detail": "First run — scaffolding installable sub-packages",
                        "path": str(pkg_root)})
        try:
            from essence.installer import install_packages as _install_packages  # lazy — avoids circular import
            _install_packages(pkg_root)
        except Exception as e:
            log.debug("scaffold_packages_error", extra={"error": str(e)[:80]})

    created = []
    entries = [
        (ws/"config.toml",                         _config_toml(hw, ws)),
        (ws/"SOUL.md",                             _DEFAULT_SOUL + "\n"),
        (ws/"HEARTBEAT.md",                        _DEFAULT_HEARTBEAT + "\n"),
        (ws/"AGENTS.md",                           _DEFAULT_AGENTS + "\n"),
        (ws/"IDENTITY.md",                         _DEFAULT_IDENTITY + "\n"),
        (ws/"TOOLS.md",                            _DEFAULT_TOOLS + "\n"),
        (ws/"MEMORY.md",                           _DEFAULT_MEMORY + "\n"),
        # Structured identity files (PAI-inspired continuous self-model)
        (ws/"GOALS.md",                            _DEFAULT_GOALS + "\n"),
        (ws/"PROJECTS.md",                         _DEFAULT_PROJECTS + "\n"),
        (ws/"LEARNED.md",                          _DEFAULT_LEARNED + "\n"),
        (ws/"skills"/"morning-briefing"/"SKILL.md",_EXAMPLE_SKILL + "\n"),
        (ws/"server"/"index.html",                 _web_ui_html(ws)),
        (ws/"server"/"app.py",                     _server_app_src(ws)),
        (ws/"server"/"__init__.py",                ""),
        (ws/"essence.py",                             _workspace_main_src(ws, hw)),
        (ws/"tui_app.py",                          _TUI_SRC),
        (ws/".gitignore",
         "memory/\nsessions/\nlogs/\nmodels/\n__pycache__/\n*.pyc\n.env\nchannel_identity.json\ndiscord_cursor.json\n"),
    ]
    if hw.tier >= 3:
        _gpu_block = (
            "deploy:\n"
            "        resources:\n"
            "          reservations:\n"
            "            devices:\n"
            "              - capabilities: [gpu]"
        ) if hw.has_cuda else ""
        entries.append((ws/"docker-compose.yml", textwrap.dedent(f"""\
            services:
              essence:
                image: python:3.12-slim
                working_dir: /app
                volumes: [".:/app","{ws}/memory:/root/.essence/memory"]
                command: essence up --port 7860
                ports: ["7860:7860"]
                environment: [Essence_TIER={hw.tier}]
                depends_on: [ollama]
              ollama:
                image: ollama/ollama:latest
                ports: ["11434:11434"]
                volumes: [ollama_data:/root/.ollama]
                {_gpu_block}
            volumes:
              ollama_data:
        """)))

    for path, content in entries:
        if _new_file(path, content):
            created.append(path.relative_to(ws))

    print(f"\n{green('✓')} Workspace  {bold(str(ws))}")
    for f in created:
        print(f"  {dim('+')} {f}")
    if not created:
        print(f"  {dim('(all files exist — nothing overwritten)')}")
    print()


# ══════════════════════════════════════════════════════════════════════════════
