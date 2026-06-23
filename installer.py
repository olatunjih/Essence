"""dep installer +  package installer +  doctor."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# ── All module imports (split from monolith) ──────────────────────────────────
# Hardware / core
from essence.core.hardware import HardwareProfile, probe_hardware, print_probe  # noqa: F401
from essence.core.constants import BANNER, amber  # noqa: F401
from essence.core.registry import select_model, print_models  # noqa: F401

# Backends
from essence.backends.adapters import BackendError, OllamaBackend, _ping  # noqa: F401
from essence.backends.registry import build_provider_chain  # noqa: F401
from essence.backends.routing import ContextBudgetManager, CostTracker  # noqa: F401

# Server / UI
from essence.server.app import run_chat, run_server  # noqa: F401
from essence.server.web_ui import SkillMarketplace  # noqa: F401

# Workspace
from essence.workspace.scaffold import (  # noqa: F401
    scaffold, load_ws_file, _DEFAULT_SOUL, _DEFAULT_TOOLS,
)
from essence.workspace.skill_system import load_skills_index  # noqa: F401
from essence.workspace.gulper import skill_gulp, skill_gulp_dir  # noqa: F401
from essence.workspace.benchmark import benchmark  # noqa: F401
from essence.workspace.sop import get_sop_loader  # noqa: F401

# Updater
from essence.updater import self_update  # noqa: F401

# Channels
from essence.channels.extended import ChannelRegistry  # noqa: F401
from essence.channels.bridge import SystemBridge  # noqa: F401

# Protocols
from essence.protocols.a2a import A2APeerRegistry, A2APeerDiscovery  # noqa: F401

# Security / sandbox
from essence.security.sandbox import (  # noqa: F401
    _CONTAINER_ENABLED, _detect_container_runtime, get_container_sandbox,
    _EphemeralContainerSandbox as ContainerSandbox,
)

# Memory
from essence.memory.memory import Memory  # noqa: F401
from essence.memory.team_sync import _TEAM_SYNC_ENABLED  # noqa: F401

# Agents
from essence.agents.agent import Agent  # noqa: F401
from essence.agents.config import AgentConfig  # noqa: F401
from essence.agents.workflow import WorkflowEngine, StepStatus  # noqa: F401
from essence.agents.decision import DecisionQueue  # noqa: F401
from essence.agents.eval import EvalHarness  # noqa: F401

# Tools
from essence.tools.registry import TOOL_REGISTRY  # noqa: F401

# Infra
from essence.infra.export import WorkspaceExporter  # noqa: F401

# LiteLLM availability flag (the list _LITELLM_PKGS is used for install_deps;
# the bool _LITELLM is used for doctor / conditional checks)
try:
    import litellm as _litellm_mod  # type: ignore  # noqa: F401
    _LITELLM: bool = True
except ImportError:
    _LITELLM = False

# Module-level event bus sentinel (set to an active instance by run_server/run_chat)
_event_bus: object | None = None

# DEPENDENCY INSTALLER
# ══════════════════════════════════════════════════════════════════════════════

_BASE_DEPS = [
    "psutil>=5.9",
    # structured logging (used by _setup_logging)
    "python-json-logger>=2.0",
    "fastapi>=0.111", "uvicorn[standard]>=0.29",
    "openai>=1.30", "tiktoken>=0.7",
    "textual>=0.63",
    "huggingface_hub>=0.23",
    # structured output + repair
    "pydantic>=2.0",
    # data science & ML core
    "pandas>=2.0", "numpy>=1.24",
    "scikit-learn>=1.4",
    "scipy>=1.12",
    "matplotlib>=3.8",
    "joblib>=1.3",
    # document ingestion (RAG)
    "pypdf>=4.0",
    "python-docx>=1.0",
    "beautifulsoup4>=4.12",
    # NLP / sentiment
    "vaderSentiment>=3.3",
    # v16: AES-256-GCM vault encryption (PRODUCTION — replaces XOR fallback)
    "cryptography>=42.0",
    # v16: async HTTP client (non-blocking FastAPI endpoints, A2A protocol, channels)
    "httpx>=0.27",
]
_T1_DEPS   = [
    "faiss-cpu>=1.7", "sqlite-vec>=0.1",
    # vision T1
    "Pillow>=10.0", "pytesseract>=0.3",
    # v16: voice pipeline — STT (Whisper) + TTS (Kokoro)
    "faster-whisper>=1.0",
    "kokoro-onnx>=0.4",
    "sounddevice>=0.4",          # audio playback for TTS
    "soundfile>=0.12",           # WAV I/O for TTS output
    "pyttsx3>=2.90",             # TTS fallback (system voices)
    "librosa>=0.10",
    # v16: browser automation (headless Chromium + Playwright)
    "playwright>=1.44",          # after install: playwright install chromium
    # v16: computer use (desktop automation)
    "pyautogui>=0.9",
    "pyperclip>=1.8",            # Unicode clipboard paste for computer_type
    # ONNX inference backend (Alpine / Pi-class fallback)
    "optimum[onnxruntime]>=1.18",
    # in-process llama.cpp Python binding (T0/T1 CPU inference)
    "llama-cpp-python>=0.2",
    # semantic memory embeddings at T1+ (22 MB, CPU-fast)
    "sentence-transformers>=2.7",
]
_T2_DEPS   = [
    "mlx-lm>=0.19",              # Apple Silicon
    # vision T2
    "ultralytics>=8.0",          # YOLOv8
    # forecasting T2
    "prophet>=1.1",
    "statsmodels>=0.14",
    # experiment tracking
    "mlflow>=2.10",
    # training
    "optuna>=3.5",
    "trl>=0.8",
    "peft>=0.9",
    "datasets>=2.18",
    "transformers>=4.40",
]
_T2_APPLE  = ["mlx-lm>=0.19"]
_T3_CUDA   = [
    "vllm>=0.7", "sglang>=0.4", "qdrant-client>=1.9",
    # T3 training
    "unsloth>=2024.3",
    "bitsandbytes>=0.43",
    # deep vision
    "paddlepaddle>=2.6", "paddleocr>=2.7",
]
_LITELLM_PKGS = ["litellm>=1.35"]
_WANDB     = ["wandb>=0.16"]


def install_deps(hw: HardwareProfile, quiet: bool = False) -> None:
    pkgs = _BASE_DEPS.copy()
    if hw.tier >= 1:              pkgs += _T1_DEPS
    if hw.tier >= 2:              pkgs += _T2_DEPS
    if hw.has_metal:              pkgs += _T2_APPLE
    if hw.tier >= 3 and hw.has_cuda: pkgs += _T3_CUDA
    # Optional cloud/tracking integrations
    if os.environ.get("Essence_INSTALL_LITELLM", ""):
        pkgs += _LITELLM_PKGS
    if os.environ.get("WANDB_API_KEY", ""):
        pkgs += _WANDB
    print(BANNER)
    print(f"  {bold('Installing deps for')} {cyan(hw.tier_label)}\n")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + pkgs
    if quiet: cmd.append("-q")
    subprocess.run(cmd, check=True, timeout=600)
    if hw.tier >= 1 and not shutil.which("ollama"):
        print(f"\n  {yellow('⚠  Ollama not installed.')}")
        if hw.os_name in ("Linux", "Darwin"):
            print("     curl -fsSL https://ollama.com/install.sh | sh")
        else:
            print("     https://ollama.com/download/windows")

    # v16: post-install steps for packages that need extra setup
    if hw.tier >= 1:
        try:
            import playwright  # type: ignore  # noqa: F401
            # Check if Chromium browser binary is installed
            result = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
                capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                print(f"\n  {yellow('⚠  Playwright installed but browser not downloaded.')}")
                print(f"     Run: {cyan('playwright install chromium')}")
        except ImportError:
            pass

    if not _AESGCM:
        print(f"\n  {yellow('⚠  cryptography not installed — vault uses XOR fallback.')}")
        print(f"     Run: {cyan('pip install cryptography')}  for AES-256-GCM encryption.")

    print(f"\n  {green('✓')} Done.")


# ══════════════════════════════════════════════════════════════════════════════

# PACKAGE INSTALLER
# ══════════════════════════════════════════════════════════════════════════════
# This command scaffolds a proper Python package workspace from the single
# monolith file.  After running, each sub-package can be pip-installed and
# developed independently while the monolith remains the canonical source.
#
#   essence install-packages [--output-dir DIR]
#
# Generated structure:
#   essence-packages/
#     essence-core/          ← hardware probe, model registry, backend adapters
#     essence-agents/        ← Agent, TaskPipeline, specialist pool, workflow engine
#     essence-memory/        ← Memory, ChromaDB backend, MemoryMigrator
#     essence-ml/            ← EDA, clustering, regression, forecasting, fine-tune
#     essence-server/        ← FastAPI server, web UI
#     essence-channels/      ← all channel adapters + ChannelRegistry
#     essence-cli/           ← CLI entry point, doctor, self-update

_PACKAGE_MANIFEST = {
    "essence-core": {
        "description": "Hardware detection, model registry, backend adapters",
        "deps": ["pydantic>=2.0"],
        "modules": ["probe", "registry", "backends", "security", "providers"],
    },
    "essence-agents": {
        "description": "Agent, TaskPipeline, SpecialistAgents, WorkflowEngine",
        "deps": ["essence-core"],
        "modules": ["agent", "workflow", "specialist", "decision_queue",
                    "observer", "verifier", "proactive", "eval"],
    },
    "essence-memory": {
        "description": "Three-layer memory: working, episodic, semantic (ChromaDB)",
        "deps": ["essence-core"],
        "extras": {"vector": ["chromadb"]},
        "modules": ["memory", "migrator"],
    },
    "essence-ml": {
        "description": "ML tools: EDA, clustering, regression, forecasting, fine-tune",
        "deps": ["essence-core"],
        "extras": {"ml": ["scikit-learn", "pandas", "numpy", "prophet"],
                   "dl": ["torch", "transformers", "peft", "unsloth"]},
        "modules": ["ml_tools", "finetune", "vision"],
    },
    "essence-server": {
        "description": "FastAPI server + web UI + MCP endpoint",
        "deps": ["essence-agents", "essence-memory", "fastapi", "uvicorn[standard]"],
        "modules": ["server", "web_ui", "mcp_server"],
    },
    "essence-channels": {
        "description": "Messaging channel adapters: Telegram, Discord, WhatsApp, Gmail, Slack",
        "deps": ["essence-core"],
        "modules": ["channels"],
    },
    "essence-cli": {
        "description": "CLI entry point, doctor, self-update, bench",
        "deps": ["essence-core", "essence-agents", "essence-channels"],
        "modules": ["cli"],
    },
}


def install_packages(output_dir: Path | None = None) -> None:
    """
    Scaffold pip-installable sub-packages from the Essence monolith.
    Each package gets a pyproject.toml with proper metadata and dependencies.
    Source modules are stubs that import from the parent monolith until
    the developer chooses to extract the actual code.
    """
    root = output_dir or (Path.cwd() / "essence-packages")
    root.mkdir(parents=True, exist_ok=True)
    print(BANNER)
    print(f"  {bold('Scaffolding Essence sub-packages')} → {cyan(str(root))}\n")

    created = []
    for pkg_name, meta in _PACKAGE_MANIFEST.items():
        pkg_dir = root / pkg_name
        src_dir = pkg_dir / pkg_name.replace("-", "_")
        src_dir.mkdir(parents=True, exist_ok=True)

        # ── pyproject.toml ────────────────────────────────────────────────
        deps_str = "\n".join(f'    "{d}",' for d in meta.get("deps", []))
        extras_toml = ""
        for extra_name, extra_deps in meta.get("extras", {}).items():
            extra_deps_str = "\n".join(f'    "{d}",' for d in extra_deps)
            extras_toml += (
                f"\n[project.optional-dependencies]\n"
                f"{extra_name} = [\n{extra_deps_str}\n]\n")

        pyproject = textwrap.dedent(f"""            [build-system]
            requires = ["hatchling"]
            build-backend = "hatchling.build"

            [project]
            name = "{pkg_name}"
            version = "{Essence_VERSION}"
            description = "{meta['description']}"
            readme = "README.md"
            requires-python = ">=3.10"
            license = {{text = "Apache-2.0"}}
            dependencies = [
            {deps_str}
            ]
            {extras_toml}
            [project.scripts]
            essence = "essence_cli.main:main"

            [tool.hatch.build.targets.wheel]
            packages = ["{pkg_name.replace('-', '_')}"]
        """)
        (pkg_dir / "pyproject.toml").write_text(pyproject, encoding="utf-8")

        # ── README.md ─────────────────────────────────────────────────────
        readme = textwrap.dedent(f"""            # {pkg_name}

            {meta['description']}

            Part of the [Essence](https://github.com/essence-project/essence) v{Essence_VERSION} system.

            ## Install

            ```bash
            pip install {pkg_name}
            ```

            ## Usage

            See the [Essence documentation](https://github.com/essence-project/essence/docs/).
        """)
        (pkg_dir / "README.md").write_text(readme, encoding="utf-8")

        # ── __init__.py stub ──────────────────────────────────────────────
        init_lines = [
            f'"""',
            f"{pkg_name} — {meta['description']}",
            f'"""',
            f"# This stub imports from the Essence monolith.",
            f"# Replace with extracted module code when splitting the monolith.",
            f"",
            f"try:",
            f"    import essence  # type: ignore  # noqa: F401",
            f"except ImportError:",
            f"    pass",
        ]
        (src_dir / "__init__.py").write_text(
            "\n".join(init_lines), encoding="utf-8")

        # ── Module stubs ───────────────────────────────────────────────────
        for mod in meta.get("modules", []):
            mod_file = src_dir / f"{mod}.py"
            if not mod_file.exists():
                mod_file.write_text(
                    f'"""\n{pkg_name}.{mod}\nExtract from essence.\n"""\n',
                    encoding="utf-8")

        created.append(pkg_name)
        print(f"  {green('✓')} {pkg_name:<22} {dim(str(pkg_dir))}")

    # ── Workspace-level pyproject.toml for monorepo tooling ───────────────
    workspace_toml = textwrap.dedent(f"""        # Essence monorepo workspace — hatch multi-project
        # Run: pip install -e ".[all]" from each sub-package directory,
        # or use: hatch env create && hatch run essence:chat
        [tool.hatch.envs.essence]
        dependencies = [
            {chr(10).join(f'    "essence-{n}",' for n in ["core","agents","memory","ml","server","channels","cli"])}
        ]
    """)
    (root / "pyproject.toml").write_text(workspace_toml, encoding="utf-8")

    print(f"\n  {bold('Sub-packages (optional pip-installable stubs):')}")
    print(f"    {dim('pip install -e ' + str(root / 'essence-core') + ' ' + str(root / 'essence-agents') + ' ' + str(root / 'essence-memory'))}")
    print(f"    {dim('pip install -e ' + str(root / 'essence-cli'))}")
    print(f"  {dim('These are stubs -- workspace essence already works standalone.')}")


# ══════════════════════════════════════════════════════════════════════════════

# DOCTOR
# ══════════════════════════════════════════════════════════════════════════════
# Checks all systems and reports green/yellow/red status:
#   Python version, Ollama, MLX/vLLM/llama.cpp, disk space, memory,
#   channel credentials, vault, required packages, network connectivity.

def doctor(ws: Path, hw: HardwareProfile) -> bool:
    """
    Run a comprehensive pre-flight health check.
    Returns True if all checks pass (or only have warnings), False if any fail.
    """
    print(BANNER)
    print(f"  {bold('Essence Doctor  v' + Essence_VERSION)}  — pre-flight health check\n")
    all_ok  = True
    PASS  = green("PASS")
    WARN  = yellow("WARN")
    FAIL  = red("FAIL")

    def check(label: str, passed: bool | None, detail: str = "",
               warn_only: bool = False) -> None:
        nonlocal all_ok
        if passed is True:
            icon = PASS
        elif passed is False:
            icon = WARN if warn_only else FAIL
            if not warn_only:
                all_ok = False
        else:
            icon = yellow(" -- ")
        print(f"  {icon}  {label:<38} {dim(detail)}")

    # ── Python version ────────────────────────────────────────────────────
    py_ok = sys.version_info >= MIN_PYTHON
    check("Python version",
          py_ok,
          f"{platform.python_version()} (need ≥{'.'.join(map(str, MIN_PYTHON))})")

    # ── Hardware tier ─────────────────────────────────────────────────────
    check("Hardware tier", True, f"{hw.tier_label}  {hw.effective_gb:.0f} GB")

    # ── Ollama ────────────────────────────────────────────────────────────
    ollama_ok = shutil.which("ollama") is not None
    check("Ollama binary", ollama_ok, "not found — run: brew install ollama"
          if not ollama_ok else "found")
    if ollama_ok:
        ollama_alive = _ping(f"{_OLLAMA_HOST}/api/tags", t=3)
        check("Ollama server",
              ollama_alive,
              f"{_OLLAMA_HOST}" + ("" if ollama_alive else " — start: ollama serve"),
              warn_only=True)

    # ── Model availability ────────────────────────────────────────────────
    try:
        ob = OllamaBackend()
        model_list = ob.list_models() if ob.alive() else []
        model_ok = any(hw.model in m for m in model_list)
        check("Recommended model",
              model_ok if model_list else None,
              f"{hw.model}" + (
                  "" if model_ok else f" — pull: essence pull {hw.model}"),
              warn_only=True)
    except Exception:
        check("Recommended model", None, "could not query Ollama", warn_only=True)

    # ── Workspace ─────────────────────────────────────────────────────────
    ws_ok = ws.exists()
    check("Workspace directory", ws_ok, str(ws))
    if ws_ok:
        usage  = shutil.disk_usage(str(ws))
        free_g = usage.free / 1e9
        pct    = 100 * usage.used / max(usage.total, 1)
        disk_ok = pct < 90
        check("Disk space",
              disk_ok,
              f"{free_g:.1f} GB free ({pct:.0f}% used)",
              warn_only=pct < 95)

    # ── Required packages ─────────────────────────────────────────────────
    pkg_checks = [
        # core
        ("pydantic",        "pydantic",           False),
        ("fastapi",         "fastapi",            True),
        ("uvicorn",         "uvicorn",            True),
        ("pytest",          "pytest",             True),
        # v16: security — AES-256-GCM vault encryption
        ("cryptography",    "cryptography",       True),
        # v16: async HTTP — non-blocking FastAPI + A2A protocol
        ("httpx",           "httpx",              True),
        # optional but recommended
        ("chromadb",        "chromadb",           True),
    ]
    for import_name, pip_name, warn in pkg_checks:
        try:
            __import__(import_name)
            check(f"Package: {pip_name}", True)
        except ImportError:
            check(f"Package: {pip_name}", False,
                  f"pip install {pip_name}",
                  warn_only=warn)

    # ── v16: Optional capability packages ─────────────────────────────────
    opt_checks = [
        ("playwright",     "playwright",      "browser automation — pip install playwright && playwright install chromium"),
        ("pyautogui",      "pyautogui",       "computer use — pip install pyautogui pillow; set Essence_COMPUTER_USE=1"),
        ("pyperclip",      "pyperclip",       "Unicode computer_type — pip install pyperclip"),
        ("faster_whisper", "faster-whisper",  "voice STT — pip install faster-whisper"),
        ("kokoro_onnx",    "kokoro-onnx",     "voice TTS — pip install kokoro-onnx sounddevice"),
        ("sounddevice",    "sounddevice",     "audio playback for TTS — pip install sounddevice"),
    ]
    for import_name, pip_name, detail in opt_checks:
        try:
            __import__(import_name)
            check(f"Optional: {pip_name}", True, "installed")
        except ImportError:
            check(f"Optional: {pip_name}", None,
                  f"not installed ({detail})",
                  warn_only=True)
    # ── Channel credentials ───────────────────────────────────────────────
    ch_registry = ChannelRegistry.from_env()
    for a in ch_registry._adapters:
        check(f"Channel: {a.NAME}",
              a.available() or None,
              "configured" if a.available() else "not configured (optional)",
              warn_only=True)

    # ── Secrets vault ─────────────────────────────────────────────────────
    vault_path = ws / ".essence_vault"
    check("Secrets vault",
          vault_path.exists() or None,
          str(vault_path) if vault_path.exists() else "not yet created (optional)",
          warn_only=True)

    # v16: report vault encryption strength
    if _AESGCM:
        check("Vault encryption",  True,  "AES-256-GCM (production)")
    else:
        check("Vault encryption",  False,
              "XOR fallback — run: pip install cryptography",
              warn_only=True)

    # v16: A2A protocol readiness
    check("A2A protocol",   True,  "enabled (GET /.well-known/agent.json when server is up)")

    # v17: A2A peer registry
    try:
        peer_reg = A2APeerRegistry(ws)
        n_peers  = len(peer_reg.all_peers())
        check("A2A peer registry",
              True if n_peers > 0 else None,
              f"{n_peers} peer(s) known" if n_peers > 0
              else "no peers yet — set Essence_A2A_PEERS or: essence peers --add URL",
              warn_only=True)
    except Exception as _e:
        check("A2A peer registry", None,
              f"registry unavailable: {str(_e)[:60]}",
              warn_only=True)

    # v17: container sandbox — uses module-level _detect_container_runtime()
    try:
        rt = _detect_container_runtime()     # module-level function
        if _CONTAINER_ENABLED:
            cs = get_container_sandbox(ws)   # module-level singleton
            if cs.available():
                check("Container sandbox", True, cs.status())
            else:
                check("Container sandbox", False,
                      "Essence_CONTAINER=1 but no runtime found — install docker/podman/nerdctl",
                      warn_only=True)
        else:
            rt_found = f"runtime: {rt}" if rt else "no runtime in PATH"
            check("Container sandbox", None,
                  f"disabled ({rt_found}) — set Essence_CONTAINER=1 to enable OS-level isolation",
                  warn_only=True)
    except Exception as _e:
        check("Container sandbox", None,
              f"check failed: {str(_e)[:60]}",
              warn_only=True)

    # v16: MCP server
    check("MCP server",     True,  "enabled (POST /mcp — connect Claude Desktop, Cursor, etc.)")

    # v16: MCP client
    mcp_srv_raw = os.environ.get("MCP_SERVERS", "")
    if mcp_srv_raw:
        try:
            _mc = __import__('json').loads(mcp_srv_raw)
            check("MCP client", True, f"{len(_mc)} server(s) configured via MCP_SERVERS")
        except Exception:
            check("MCP client", False, "MCP_SERVERS env var is set but not valid JSON", warn_only=True)
    else:
        check("MCP client", None, "no MCP_SERVERS configured (optional)", warn_only=True)

    # v16: voice pipeline
    vp_ok = False
    try:
        from faster_whisper import WhisperModel  # type: ignore  # noqa: F401
        vp_ok = True
    except ImportError:
        pass
    check("Voice pipeline (STT)", vp_ok or None,
          "faster-whisper available" if vp_ok
          else "not installed — pip install faster-whisper",
          warn_only=True)

    # v16: browser tool
    bw_ok = False
    try:
        from playwright.sync_api import sync_playwright  # type: ignore  # noqa: F401
        bw_ok = True
    except ImportError:
        pass
    check("Browser tool (Playwright)", bw_ok or None,
          "playwright available" if bw_ok
          else "not installed — pip install playwright && playwright install chromium",
          warn_only=True)

    # ── Channel adapters ──────────────────────────────────────────────────
    tg_ok = bool(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    check("Telegram adapter", tg_ok or None,
          "TELEGRAM_BOT_TOKEN set" if tg_ok
          else "not configured — set TELEGRAM_BOT_TOKEN to enable",
          warn_only=True)
    dc_ok = bool(os.environ.get("DISCORD_WEBHOOK_URL", "") or
                 os.environ.get("DISCORD_BOT_TOKEN", ""))
    check("Discord adapter", dc_ok or None,
          "DISCORD_WEBHOOK_URL or DISCORD_BOT_TOKEN set" if dc_ok
          else "not configured — set DISCORD_WEBHOOK_URL to enable",
          warn_only=True)

    # ── Hybrid memory ─────────────────────────────────────────────────────
    sv_ok = False
    try:
        import sqlite_vec  # type: ignore  # noqa: F401
        sv_ok = True
    except ImportError:
        pass
    fi_ok = False
    try:
        import faiss  # type: ignore  # noqa: F401
        fi_ok = True
    except ImportError:
        pass
    if hw.tier >= 3:
        check("Memory backend (Qdrant)", None,
              "T3: install qdrant-client + run Qdrant container for best recall",
              warn_only=True)
    elif hw.tier >= 1:
        check("Memory backend (FAISS)", fi_ok or None,
              "faiss-cpu available — hybrid BM25+vector enabled" if fi_ok
              else "not installed — pip install faiss-cpu",
              warn_only=True)
    else:
        check("Memory backend (sqlite-vec)", sv_ok or None,
              "sqlite-vec available — hybrid BM25+vector enabled" if sv_ok
              else "falling back to BM25-only JSON store — pip install sqlite-vec",
              warn_only=True)

    # ── Container runtime ─────────────────────────────────────────────────
    rt = _detect_container_runtime()
    check("Container runtime",
          rt is not None or None,
          f"{rt} (found)" if rt else "not found — install Docker/Podman (optional)",
          warn_only=True)

    # ── Vault strength ────────────────────────────────────────────────────
    check("Vault encryption",
          _AESGCM,
          "AES-256-GCM (strong)" if _AESGCM
          else "XOR fallback — run: pip install cryptography",
          warn_only=not _AESGCM)

    # ── Team namespace ────────────────────────────────────────────────────
    team_ws = ws / "team" / _TEAM_ID if _TEAM_ID != "local" else None
    check("Team namespace",
          _TEAM_ID != "local" or None,
          f"Essence_TEAM_ID={_TEAM_ID} (shared)" if _TEAM_ID != "local"
          else "local (per-user) — set Essence_TEAM_ID for team sharing",
          warn_only=True)

    # ── SOP directory ─────────────────────────────────────────────────────
    sop_p = Path(_SOP_DIR) if _SOP_DIR else ws / "procedures"
    sop_count = len(list(sop_p.glob("*.md"))) if sop_p.exists() else 0
    check("SOP procedures",
          sop_count > 0 or None,
          f"{sop_count} SOP(s) in {sop_p}" if sop_count > 0
          else f"none found in {sop_p} (optional)",
          warn_only=True)

    # ── Cost log ──────────────────────────────────────────────────────────
    cost_log = ws / "cost_log.jsonl"
    cost_lines = 0
    if cost_log.exists():
        try:
            cost_lines = sum(1 for _ in cost_log.open())
        except Exception:
            pass
    check("Cost log",
          cost_lines >= 0 or None,
          f"{cost_lines} task records in {cost_log}" if cost_lines
          else "no tasks logged yet (writes on first task run)",
          warn_only=True)

    # ── Network ───────────────────────────────────────────────────────────
    net_ok = _ping("https://api.github.com", t=5)
    check("Network (github.com)", net_ok or None,
          "reachable" if net_ok else "unreachable (offline mode OK)",
          warn_only=True)

    # ══ v20 system checks ════════════════════════════════════════════════
    print(f"\n  {dim('── v20 v20 systems ──────────────────────────────────')}")

    # ── SemanticStateStore ────────────────────────────────────────────────
    sss_path  = ws / "memory" / "semantic_state.json"
    sss_count = 0
    if sss_path.exists():
        try:
            raw = json.loads(sss_path.read_text(encoding="utf-8"))
            sss_count = len(raw) if isinstance(raw, list) else 0
        except Exception:
            pass
    check("SemanticStateStore",
          sss_count >= 0 or None,
          f"{sss_count} fact(s) in {sss_path.name}"
          if sss_path.exists() else "not yet written (first chat creates it)",
          warn_only=True)

    # ── ContextBudgetManager ─────────────────────────────────────────────
    _ctx_win  = {0: 4096, 1: 8192, 2: 32768, 3: 131072}.get(hw.tier, 8192)
    _cb       = ContextBudgetManager(context_window=_ctx_win)
    check("ContextBudgetManager", True, _cb.summary())

    # ── ContextualBanditRouter ────────────────────────────────────────────
    bandit_path = ws / "logs" / "bandit_state.json"
    bandit_arms = 0
    if bandit_path.exists():
        try:
            bandit_arms = len(json.loads(bandit_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    check("ContextualBanditRouter",
          bandit_arms >= 0 or None,
          f"{bandit_arms} arm observations" if bandit_arms
          else "cold start — UCB will fall back to A/B until min observations",
          warn_only=True)

    # ── LiteLLM backend ───────────────────────────────────────────────────
    check("LiteLLM provider shim",
          _LITELLM or None,
          "available" if _LITELLM else
          "not installed (optional) — pip install litellm",
          warn_only=True)

    # ── TeamMemorySync ────────────────────────────────────────────────────
    team_enabled = _TEAM_SYNC_ENABLED and _TEAM_ID != "local"
    _peer_urls   = [u.strip() for u in
                    os.environ.get("Essence_A2A_PEERS", "").split(",") if u.strip()]
    check("TeamMemorySync",
          team_enabled or None,
          f"enabled — namespace={_TEAM_ID}, peers={len(_peer_urls)}"
          if team_enabled else
          "disabled (set Essence_TEAM_SYNC=1 + Essence_TEAM_ID=<ns> + Essence_A2A_PEERS=...)",
          warn_only=True)

    # ── DAGWorkflowExecutor ───────────────────────────────────────────────
    wf_dir   = ws / "workflows"
    wf_count = len(list(wf_dir.glob("wf_*.json"))) if wf_dir.exists() else 0
    check("DAGWorkflowExecutor", True,
          f"{wf_count} workflow(s) in history  /workflow-editor for DAG view")

    # ── Skill composition (use_skill) ─────────────────────────────────────
    use_skill_registered = TOOL_REGISTRY.get_handler("use_skill") is not None
    check("use_skill tool",
          use_skill_registered,
          "registered — skills can invoke each other (cycle detection active)"
          if use_skill_registered else "not registered (check bootstrap)")

    # ── Streaming tool results ────────────────────────────────────────────
    _bus_active = _event_bus is not None
    check("WebhookEventBus (tool stream)",
          _bus_active or None,
          "active — tool results stream to WebSocket clients"
          if _bus_active else
          "not yet started (auto-starts on 'essence up')",
          warn_only=True)

    # ── workspace-editor route ────────────────────────────────────────────
    check("Workflow DAG editor", True,
          "available at /workflow-editor when server is running")

    print()
    if all_ok:
        print(f"  {green('✓ All required checks passed.')}")
    else:
        print(f"  {red('✗ Some checks failed.')} Fix the FAIL items above.")
    print()
    return all_ok



def _check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        v = ".".join(str(x) for x in MIN_PYTHON)
        print(f"Essence requires Python {v}+. Found {platform.python_version()}")
        sys.exit(1)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="essence.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=f"Essence v{Essence_VERSION} — Essence Intelligence System",
        epilog=textwrap.dedent("""\
            Quick start:
              essence               → probe + scaffold workspace
              essence install       → install tier deps
              essence pull qwen3:8b → download model
              essence chat          → start chatting
              essence up            → web UI + API at :7860
        """))
    p.add_argument("--version", action="version",
                   version=f"Essence v{Essence_VERSION} ({Essence_BUILD})")
    p.add_argument("--workspace", metavar="DIR")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("probe",            help="Hardware tier + model recommendation")
    sub.add_parser("install",          help="Install deps for detected tier")
    sub.add_parser("scaffold",         help="(Re)generate workspace directory")
    sub.add_parser("models",           help="Model registry for current tier")
    sub.add_parser("bench",            help="Benchmark available backends")
    sub.add_parser("self-update",      help="Pull latest release from GitHub")
    sub.add_parser("channels",         help="List messaging channel adapter status")
    sub.add_parser("doctor",           help="Pre-flight health check (all systems)")
    # ── install-packages ──────────────────────────────────────────────────────
    pip = sub.add_parser("install-packages",
        help="Scaffold pip-installable sub-packages from this monolith")
    pip.add_argument("--output-dir", metavar="DIR", default="",
                     help="Output directory (default: ./essence-packages)")
    # ── skill management ──────────────────────────────────────────────────────
    psk = sub.add_parser("skill",
        help="Skill management: new · list · install · import · gulp · gulp-dir")
    psk.add_argument("skill_action",
                     choices=["new", "list", "install", "import", "gulp", "gulp-dir"],
                     help=("new NAME: scaffold  |  list: show installed  |  "
                           "install URL/NAME: install from URL/registry  |  "
                           "import URL: alias for install with richer progress feedback  |  "
                           "gulp URL: absorb from any open-source URL/format  |  "
                           "gulp-dir PATH: bulk-ingest local directory"))
    psk.add_argument("skill_arg", nargs="?", default="",
                     help="Skill name / URL / path (for new/install/import/gulp/gulp-dir)")

    pp = sub.add_parser("pull",  help="Pull a model via Ollama")
    pp.add_argument("model_tag")

    pc = sub.add_parser("chat",  help="Interactive streaming terminal chat")
    pc.add_argument("--model",  default="",
                    help="Model tag (omit for interactive picker)")
    pc.add_argument("--think",  action="store_true", help="Enable reasoning mode")
    pc.add_argument("--budget", type=int, default=1024)
    pc.add_argument("--role",   default="",
                    choices=["standalone","orchestrator","worker","router","master","slave","intermediary"],
                    help="System role (overrides Essence_ROLE env var)")

    sub.add_parser("tui", help="Textual TUI dashboard")

    pu = sub.add_parser("up",    help="FastAPI server + full web UI")
    pu.add_argument("--port",  type=int, default=7860)
    pu.add_argument("--model", default="")
    pu.add_argument("--role",  default="",
                    choices=["standalone","orchestrator","worker","router","master","slave","intermediary"],
                    help="System role (overrides Essence_ROLE env var)")

    pa = sub.add_parser("agent", help="One-shot multi-agent task (TaskPipeline)")
    pa.add_argument("task",     nargs="+")
    pa.add_argument("--model",  default="",
                    help="Model tag (omit for interactive picker)")
    pa.add_argument("--think",  action="store_true")
    pa.add_argument("--no-critic", action="store_true",
                    help="Disable CriticGate critic gate")
    pa.add_argument("--autonomy", type=int, default=1, choices=[0, 1, 2],
                    help="0=confirm every tool call, 1=confirm destructive only, "
                         "2=fully autonomous")
    pa.add_argument("--role",  default="",
                    choices=["standalone","orchestrator","worker","router","master","slave","intermediary"],
                    help="System role (overrides Essence_ROLE env var)")

    # ── Eval harness ──────────────────────────────────────────────────────────
    pe = sub.add_parser("eval",
        help="Run behavioral eval harness (safety + competence scenarios)")
    pe.add_argument("--model",   default="", help="Model tag to evaluate")
    pe.add_argument("--save-baseline", action="store_true",
                    help="Save results as regression baseline")
    pe.add_argument("--regression",    action="store_true",
                    help="Compare against saved baseline and exit 1 on drop")
    pe.add_argument("--drift",         action="store_true",
                    help="Run drift_check: semantic comparison against baseline with "
                         "per-scenario delta and optional webhook notification "
                         "(Essence_DRIFT_WEBHOOK)")
    pe.add_argument("--scenario", default="",
                    help="Run a single named scenario instead of all")

    # ── Decision queue review ─────────────────────────────────────────────────
    pd = sub.add_parser("decisions",
        help="Review and approve/reject pending tool-call decisions")
    pd.add_argument("action", nargs="?", default="list",
                    choices=["list","approve","reject","approve-all"],
                    help="list|approve <id>|reject <id>|approve-all")
    pd.add_argument("decision_id", nargs="?", default="",
                    help="Decision ID for approve/reject")

    # ── Workflow status ───────────────────────────────────────────────────────
    pw = sub.add_parser("workflows",
        help="List recent workflows and their step status")
    pw.add_argument("--id", default="",
                    help="Show detail for a specific workflow ID")

    # ── A2A peer registry ─────────────────────────────────────────────────────
    ppr = sub.add_parser("peers",
        help="List/probe known A2A peer agents")
    ppr.add_argument("--probe", action="store_true",
                     help="Re-probe all known peers for reachability")
    ppr.add_argument("--add", default="",
                     help="Manually add a peer by base URL")
    ppr.add_argument("--remove", default="",
                     help="Remove a peer by base URL")

    # ── Control (alias: open web UI or show server status) ────────────────
    pctl = sub.add_parser("control",
        help="Open the web Control Panel, or print server status URL")
    pctl.add_argument("--port", type=int, default=7860,
                      help="Port the server is running on (default 7860)")
    pctl.add_argument("--open", action="store_true", default=False,
                      help="Open the browser immediately (macOS/Linux)")

    # ── v18: production commands ──────────────────────────────────────────────
    sub.add_parser("cost",  help="Token cost report and task spend history")
    sub.add_parser("team",  help="Team memory namespace info")

    pmem = sub.add_parser("memory", help="Memory bundle export / import")
    pmem.add_argument("memory_action", nargs="?", default="",
                      choices=["export", "import"],
                      help="export or import a memory bundle")
    pmem.add_argument("memory_in", nargs="?", default="",
                      help="Path to bundle file (for import)")
    pmem.add_argument("--out", dest="memory_out", default="",
                      help="Output path for export (default: workspace/memory_export.essence_bundle)")
    pmem.add_argument("--passphrase", default="",
                      help="AES-256-GCM passphrase for encrypt/decrypt")
    pmem.add_argument("--merge", action="store_true", default=False,
                      help="Merge imported bundle instead of replacing existing memory")

    sub.add_parser("sop",     help="List Standard Operating Procedures in procedures/ dir")
    pexp = sub.add_parser("export", help="Export full workspace as portable ZIP")
    pexp.add_argument("dest", nargs="?", default="",
                      help="Output ZIP path (default: parent of workspace)")

    pimp = sub.add_parser("import", help="Import workspace from ZIP")
    pimp.add_argument("src", help="Path to ZIP file")
    pimp.add_argument("--overwrite", action="store_true", default=False,
                      help="Overwrite existing files in workspace")

    sub.add_parser("keys",    help="List and manage API keys")
    sub.add_parser("plugins", help="List hot-loaded plugins")
    paudit = sub.add_parser("audit", help="Audit log operations")
    paudit.add_argument("audit_action", nargs="?", default="verify",
                        choices=["verify"],
                        help="Operation: verify (default)")
    pimp2 = sub.add_parser("import-workspace", help="Alias: import workspace from ZIP")
    pimp2.add_argument("src", help="Path to ZIP file")
    pimp2.add_argument("--overwrite", action="store_true", default=False)
    return p


def _workspace_root() -> Path:
    """Delegates to the canonical workspace_root() in essence.core.paths."""
    try:
        from essence.core.paths import workspace_root
        return workspace_root()
    except ImportError:
        return Path.home() / ".essence"


def _handle_no_backend_cmd(cmd: str, args: "argparse.Namespace",
                            ws: Path, hw: "HardwareProfile") -> int | None:
    """
    Handle CLI subcommands that do NOT require a live LLM backend.
    Returns an exit code (0 = success) if the command was handled,
    or None if the caller should proceed to build_provider_chain().
    """
    if cmd == "decisions":
        dq     = DecisionQueue(ws)
        action = getattr(args, "action", "list")
        did    = getattr(args, "decision_id", "")
        print(BANNER)
        if action == "list":
            pending = dq.pending()
            if not pending:
                print(f"  {green('No pending decisions.')}")
            else:
                print(f"  {bold(f'{len(pending)} pending decision(s):')}\n")
                for d in pending:
                    pri_col = [dim, cyan, yellow, amber, red][d.priority.value]
                    ttl_left = max(0, d.expires_at - time.time())
                    print(f"  {pri_col(d.priority.name):<12} "
                          f"{bold(d.decision_id)}  "
                          f"{cyan(d.tool_name)}({json.dumps(d.args)[:60]})")
                    print(f"  {dim(d.reason[:80])}  "
                          f"{dim(f'expires in {ttl_left:.0f}s')}\n")
        elif action == "approve" and did:
            ok = dq.approve(did)
            print(green(f"  ✓ Approved {did}") if ok
                  else red(f"  ✗ Not found or already decided: {did}"))
        elif action == "reject" and did:
            ok = dq.reject(did, reason="CLI rejection")
            print(green(f"  ✓ Rejected {did}") if ok
                  else red(f"  ✗ Not found or already decided: {did}"))
        elif action == "approve-all":
            n = dq.batch_approve([d.decision_id for d in dq.pending()])
            print(green(f"  ✓ Approved {n} decision(s)"))
        else:
            print("  Usage: decisions [list|approve <id>|reject <id>|approve-all]")
        return 0

    if cmd == "workflows":
        engine = WorkflowEngine(ws)
        print(BANNER)
        wf_id = getattr(args, "id", "")
        if wf_id:
            state = engine.resume(wf_id)
            if not state:
                print(red(f"  Workflow '{wf_id}' not found.")); sys.exit(1)
            print(f"  {bold('Workflow:')} {state.task_id}")
            print(f"  {bold('Task:    ')} {state.task[:80]}")
            print(f"  {bold('Status:  ')} {cyan(state.status.value)}\n")
            print(f"  {bold('Steps:')}")
            for s in state.steps:
                col = {StepStatus.SUCCESS: green, StepStatus.FAILED: red,
                       StepStatus.RUNNING: yellow}.get(s.status, dim)
                print(f"    {col(s.status.value.upper()[:7]):<10} "
                      f"[{s.step_id}] {s.action[:60]}")
                if s.result and s.status != StepStatus.PENDING:
                    print(f"           {dim(str(s.result)[:80])}")
        else:
            wf_ids = engine.list_workflows()
            if not wf_ids:
                print(f"  {dim('No workflows found.')}")
            else:
                print(f"  {bold(f'{len(wf_ids)} recent workflow(s):')}\n")
                for wid in wf_ids[:20]:
                    state = engine.resume(wid)
                    if state:
                        n_done = sum(1 for s in state.steps
                                     if s.status == StepStatus.SUCCESS)
                        n_tot  = len(state.steps)
                        col    = green if state.status == StepStatus.SUCCESS \
                                 else red if state.status == StepStatus.FAILED \
                                 else yellow
                        print(f"  {col(wid):<28} "
                              f"{dim(state.task[:45]):<47} "
                              f"[{n_done}/{n_tot}]")
        return 0

    if cmd == "cost":
        tracker = CostTracker(ws)
        summary = tracker.summary()
        history = tracker.history(n=20)
        print(BANNER)
        print(f"  {bold('Cost summary')}")
        print(f"  {bold('Tasks logged'  ):<22}{summary['tasks']:>8,}")
        print(f"  {bold('Total tokens'  ):<22}{summary['total_tokens']:>8,}")
        print(f"  {bold('Avg per task'  ):<22}{summary['avg_tokens']:>8,}")
        print(f"  {bold('Total tool calls'):<22}{summary['total_tool_calls']:>8,}")
        if history:
            print(f"\n  {bold('Recent tasks (newest first):')}\n")
            for rec in reversed(history[-10:]):
                tid   = rec.get('task_id', '?')[:20]
                tot   = rec.get('total_tokens', 0)
                dur   = rec.get('duration_s') or 0
                mdl   = rec.get('model', '')[:16]
                print(f"  {cyan(tid):<22} {tot:>7,} tok  {dur:>6.1f}s  {dim(mdl)}")
        budget_env = _COST_BUDGET
        if budget_env:
            print(f"\n  {bold('Budget:')} {budget_env:,} tokens/task "
                  f"(Essence_COST_BUDGET={budget_env})")
        return 0

    if cmd == "team":
        print(BANNER)
        print(f"  {bold('Team namespace'):<22}{cyan(_TEAM_ID)}")
        team_ws = ws / "team" / _TEAM_ID
        if _TEAM_ID == "local":
            print(f"  {dim('Using local (per-user) memory.')}")
            print(f"  {dim('Set Essence_TEAM_ID=<name> to share memory with teammates.')}")
        else:
            print(f"  {bold('Team workspace'):<22}{dim(str(team_ws))}")
            mem_dir = team_ws / "memory"
            if mem_dir.exists():
                kv_path = mem_dir / "kv.json"
                ep_path = mem_dir / "episodic.jsonl"
                kv_keys = 0
                if kv_path.exists():
                    try:
                        kv_keys = len(json.loads(kv_path.read_text()))
                    except Exception:
                        pass
                ep_lines = 0
                if ep_path.exists():
                    try:
                        ep_lines = sum(1 for _ in ep_path.open())
                    except Exception:
                        pass
                print(f"  {bold('KV entries'):<22}{kv_keys:>8,}")
                print(f"  {bold('Episodic records'):<22}{ep_lines:>8,}")
            else:
                print(f"  {dim('No team memory yet — write something first.')}")
        return 0

    if cmd == "memory":
        action = getattr(args, "memory_action", "")
        print(BANNER)
        if action == "export":
            out_path   = getattr(args, "memory_out", "")
            passphrase = getattr(args, "passphrase", "") or \
                         os.environ.get("Essence_MEMORY_EXPORT_KEY", "")
            mem_e  = Memory(ws, team_id=_TEAM_ID)
            bundle = mem_e.export_bundle(passphrase=passphrase)
            dest   = Path(out_path) if out_path else ws / "memory_export.essence_bundle"
            dest.write_bytes(bundle)
            enc = (" (AES-256-GCM encrypted)" if passphrase and _AESGCM else
                   " (passphrase set but AES unavailable — unencrypted)" if passphrase else
                   " (no passphrase — unencrypted)")
            print(f"  {green('✓')} Memory exported → {dest}{enc}")
            print(f"  {dim(f'{len(bundle):,} bytes')}")
        elif action == "import":
            in_path    = getattr(args, "memory_in", "")
            passphrase = getattr(args, "passphrase", "") or \
                         os.environ.get("Essence_MEMORY_EXPORT_KEY", "")
            merge = getattr(args, "merge", False)
            if not in_path:
                print(red("Usage: memory import <bundle_file> [--merge]")); sys.exit(1)
            bundle = Path(in_path).read_bytes()
            mem_e  = Memory(ws, team_id=_TEAM_ID)
            counts = mem_e.import_bundle(bundle, passphrase=passphrase, merge=merge)
            mode   = "merged into" if merge else "replaced"
            print(f"  {green('✓')} Memory bundle {mode} {dim(str(ws))}")
            for k, v in counts.items():
                print(f"  {bold(k):<22}{v:>8,}")
        else:
            print("  Usage: memory export [--out FILE] [--passphrase KEY]")
            print("         memory import <FILE> [--merge] [--passphrase KEY]")
        return 0

    if cmd == "sop":
        loader = get_sop_loader(ws / "procedures")
        docs   = loader.list_all()
        print(BANNER)
        if not docs:
            sop_dir = ws / "procedures"
            print(f"  {dim('No SOPs found.')}")
            print(f"  {dim(f'Create Markdown files in: {sop_dir}')}")
            print(f"  {dim('Frontmatter: --- triggers: [deploy, release] ---')}")
        else:
            print(f"  {bold(str(len(docs)) + ' SOP(s) loaded:')}\n")
            for d in docs:
                triggers = ", ".join(d["triggers"][:5])
                pri_col  = green if d["priority"] == "high" else \
                           cyan  if d["priority"] == "medium" else dim
                print(f"  {pri_col(d['name']):<28} "
                      f"{dim('triggers: ' + triggers)}")
        return 0

    if cmd == "export":
        _dest_str = getattr(args, "dest", "") or ""
        _dest = Path(_dest_str) if _dest_str else None
        _out  = WorkspaceExporter.export(ws, _dest)
        print(f"  Exported workspace → {_out}")
        return 0

    if cmd in ("import", "import-workspace"):
        _src_str = getattr(args, "src", "")
        if not _src_str:
            print("  Usage: essence import <file.zip>  [--overwrite]")
        else:
            _ow  = getattr(args, "overwrite", False)
            _zip = Path(_src_str)
            _res = WorkspaceExporter.import_zip(_zip, ws, overwrite=_ow)
            print(f"  Imported {len(_res['imported'])} / skipped {len(_res['skipped'])} / errors {len(_res['errors'])}")
            for err in _res["errors"]: print(f"  ERROR: {err}")
        return 0

    if cmd == "keys":
        from essence.core.vault import SecretsVault
        vault = SecretsVault(ws)
        keys  = vault.list_keys()
        print(BANNER)
        if not keys:
            print(f"  {dim('No keys stored.')}")
            print(f"  {dim('Add keys: essence keys set <name> <value>')}")
        else:
            print(f"  {bold(f'{len(keys)} key(s):')}")
            for k in keys:
                print(f"  {cyan(k)}")
        return 0

    if cmd == "plugins":
        print(BANNER)
        plugin_dir = ws / "plugins"
        plugins = list(plugin_dir.glob("*.py")) if plugin_dir.exists() else []
        if not plugins:
            print(f"  {dim('No plugins installed.')}")
            print(f"  {dim(f'Drop .py files into: {plugin_dir}')}")
        else:
            print(f"  {bold(f'{len(plugins)} plugin(s):')}")
            for p in plugins:
                print(f"  {cyan(p.stem):<28} {dim(str(p))}")
        return 0

    if cmd == "audit":
        from essence.security.audit_logger import AuditLogger
        al = AuditLogger(ws)
        action = getattr(args, "audit_action", "verify")
        print(BANNER)
        if action == "verify":
            ok = al.verify()
            if ok:
                print(f"  {green('✓')} Audit log integrity verified.")
            else:
                print(f"  {red('✗')} Audit log integrity check FAILED.")
                sys.exit(1)
        return 0

    return None  # not handled — caller should use build_provider_chain


def main(argv: list[str] | None = None) -> None:
    _check_python()
    args = _parser().parse_args(argv)
    ws   = Path(args.workspace) if getattr(args, "workspace", None) \
           else _workspace_root()
    ws.mkdir(parents=True, exist_ok=True)
    hw  = probe_hardware()
    cmd = args.cmd or ""

    # ── Apply --role CLI override before anything else ───────────────────────
    _cli_role = getattr(args, "role", "")
    if _cli_role:
        os.environ["Essence_ROLE"] = _cli_role

    # ── Default: first-run guide ────────────────────────────────────────────
    if not cmd:
        print(BANNER); print_probe(hw); scaffold(ws, hw)
        role = get_system_role()
        print(f"  {bold('System role')}  {cyan(role.value)}")
        _ws_essence = ws / 'essence.py'
        print(f"  {bold('Workspace entry point:')}")
        print(f"    {cyan(str(_ws_essence))}")
        print()
        print(f"  {bold('Next steps  (run from workspace):')}")
        print(f"    {cyan('cd ' + str(ws))}")
        print(f"    {cyan('essence install')}")
        print(f"    {cyan('essence pull ' + hw.model)}")
        print(f"    {cyan('essence chat')}")
        print(f"    {cyan('essence up')}")
        print(f"  {dim('The bootstrap installer is no longer needed.')}")
        return

    if cmd == "probe":
        print_probe(hw)
        role = get_system_role()
        print(f"  {bold('Role'):<18}{cyan(role.value)}")
        print(f"  {bold('Essence_WORKER_URLS'):<18}"
              f"{dim(os.environ.get('Essence_WORKER_URLS', os.environ.get('Essence_SLAVE_URLS','(not set)')))} "
              f"{dim('(formerly Essence_SLAVE_URLS)')}")
        print(f"  {bold('Essence_ORCH_URL'):<18}"
              f"{dim(os.environ.get('Essence_ORCH_URL', os.environ.get('Essence_MASTER_URL','(not set)')))} "
              f"{dim('(formerly Essence_MASTER_URL)')}")
        print()
        return
    if cmd == "install":          install_deps(hw); return
    if cmd == "scaffold":         scaffold(ws, hw); return
    if cmd == "models":           print_models(hw); return
    if cmd == "bench":            benchmark(hw); return
    if cmd == "self-update":      self_update(); return
    if cmd == "doctor":           doctor(ws, hw); return
    if cmd == "install-packages":
        out = Path(args.output_dir) if getattr(args, "output_dir", "")               else None
        install_packages(out); return
    if cmd == "skill":
        mkt = SkillMarketplace(ws)
        action = getattr(args, "skill_action", "list")
        arg    = getattr(args, "skill_arg", "")
        if action == "list":
            installed = mkt.list_installed()
            print(BANNER)
            if not installed:
                print(f"  {dim('No skills installed.')}  "
                      f"Try: essence skill install <n>")
            else:
                print(f"  {bold(f'{len(installed)} installed skill(s):')}")
                for s in installed:
                    src = dim(f"  [{s.source_url}]") if getattr(s, "source_url", "") else ""
                    print(f"  {green(s.name):<26} {dim(s.description[:60])}{src}")
            try:
                registry = mkt.fetch_registry()
                if registry:
                    print(f"\n  {bold(f'{len(registry)} skills in remote registry:')}")
                    for s in registry[:10]:
                        inst = green("installed") if any(
                            i.name == s.name for i in installed) else ""
                        print(f"  {cyan(s.name):<26} "
                              f"v{s.version:<8} {dim(s.description[:40])} {inst}")
            except Exception:
                pass
        elif action == "new":
            if not arg:
                print(red("Usage: essence skill new <n>")); sys.exit(1)
            mkt.scaffold_new_skill(arg)
        elif action in ("install", "import"):
            if not arg:
                print(red(f"Usage: essence skill {action} <name|url>")); sys.exit(1)
            if action == "import":
                print(f"  {cyan('⬇')}  Importing skill from {dim(arg)}")
                print(f"  {dim('Detecting format, translating, validating…')}")
            ok, msg = mkt.install_skill(arg)
            if ok:
                print(f"  {green('✓')}  {msg}")
                print(f"  {dim('Skill hot-reloaded into TOOL_REGISTRY')}")
            else:
                print(f"  {red('✗')}  {msg}")
                if action == "import":
                    print(f"  {yellow('TIP:')} Try: essence skill gulp {arg}")
                sys.exit(1)
        elif action == "gulp":
            if not arg:
                print(red("Usage: essence skill gulp <url>")); sys.exit(1)
            ok, msg = skill_gulp(arg, ws)
            print(f"  {green('✓') if ok else red('✗')} {msg}")
            if not ok:
                sys.exit(1)
        elif action == "gulp-dir":
            if not arg:
                print(red("Usage: essence skill gulp-dir <path>")); sys.exit(1)
            results = skill_gulp_dir(Path(arg), ws)
            ok_count = sum(1 for ok, _ in results if ok)
            for ok, msg in results:
                print(f"  {green('✓') if ok else red('✗')} {msg}")
            print(f"\n  {ok_count}/{len(results)} skills ingested")
            if ok_count == 0 and results:
                sys.exit(1)
        return

    if cmd == "channels":
        print(BANNER)
        reg = ChannelRegistry.from_env()
        print(f"  {bold('Channel adapters:')}\n")
        _env_hints = {
            "telegram":  "TELEGRAM_BOT_TOKEN",
            "discord":   "DISCORD_WEBHOOK_URL  or  DISCORD_BOT_TOKEN",
            "whatsapp":  "WHATSAPP_TOKEN + WHATSAPP_PHONE_ID",
            "gmail":     "GMAIL_ADDRESS + GMAIL_APP_PASSWORD",
            "slack":     "SLACK_BOT_TOKEN",
            "matrix":    "MATRIX_HOMESERVER + MATRIX_ACCESS_TOKEN",
            "teams":     "TEAMS_WEBHOOK_URL  or  TEAMS_BOT_ID + TEAMS_BOT_PASSWORD",
            "line":      "LINE_CHANNEL_TOKEN",
            "imessage":  "IMESSAGE_HANDLE  (macOS only)",
        }
        for a in reg._adapters:
            status = green("available") if a.available() else dim("not configured")
            hint   = dim(_env_hints.get(a.NAME, ""))
            print(f"  {a.NAME:<12} {status:<28} {hint}")
        active = reg.active()
        print(f"\n  {len(active)}/{len(reg._adapters)} active channels")
        return

    if cmd == "peers":
        print(BANNER)
        peer_reg   = A2APeerRegistry(ws)
        add_url    = getattr(args, "add", "")
        remove_url = getattr(args, "remove", "")
        do_probe   = getattr(args, "probe", False)
        if add_url:
            discovery = A2APeerDiscovery(peer_reg)
            ok = discovery._probe_url(add_url)
            print(f"  {green('✓ added') if ok else red('✗ unreachable')}  {add_url}")
            return
        if remove_url:
            peer_reg.remove(remove_url)
            print(f"  {dim('removed')}  {remove_url}")
            return
        if do_probe:
            discovery = A2APeerDiscovery(peer_reg)
            discovery._probe_env_peers()
            discovery._probe_existing_peers()
            print(f"  {green('probe complete')}")
        print(f"  {bold('Known A2A peers:')}\n")
        print(peer_reg.summary() or f"  {dim('No peers registered.')}")
        print(f"\n  {dim('Add peers via Essence_A2A_PEERS env var or: essence peers --add URL')}")
        return

    if cmd == "control":
        port = getattr(args, "port", 7860)
        url  = f"http://localhost:{port}"
        print(BANNER)
        print(f"  {bold('Control Panel')}  {cyan(url)}")
        print(f"  {dim('Start the server first:')}  {cyan(f'essence up --port {port}')}")
        print()
        print(f"  {bold('Quick status check:')}")
        try:
            import urllib.request as _ur
            data = json.loads(
                _ur.urlopen(f"{url}/api/status", timeout=3).read().decode())
            print(f"  {green('●')} Server online  ·  backend={data.get('backend','?')}  "
                  f"model={data.get('model','?')}  tier={data.get('tier_label','?')}")
            peers = data.get('a2a_peers', [])
            print(f"  {bold('A2A peers:')} {len(peers)} known  "
                  f"({sum(1 for p in peers if p.get('reachable',True))} reachable)")
        except Exception:
            print(f"  {yellow('○')} Server not running on port {port}")
            print(f"    Run: {cyan(f'essence up --port {port}')}")
        do_open = getattr(args, "open", False)
        if do_open:
            try:
                import webbrowser
                webbrowser.open(f"{url}/")
                print(f"  {green('→')} Opened browser: {url}")
            except Exception as e:
                print(f"  {yellow('→')} Could not open browser: {e}")
        return

    if cmd == "pull":
        if not shutil.which("ollama"):
            print(red("Ollama not installed. https://ollama.com")); sys.exit(1)
        subprocess.run(["ollama", "pull", args.model_tag], check=True, timeout=600); return

    # ── Commands that do NOT need a live LLM backend ──────────────────────
    # Execute these before build_provider_chain so they work without Ollama.
    _no_backend_result = _handle_no_backend_cmd(cmd, args, ws, hw)
    if _no_backend_result is not None:
        return

    # ── Commands requiring a live backend ───────────────────────────────────
    try:
        prov = build_provider_chain(hw)
    except BackendError as e:
        print(red(f"Backend error: {e}")); sys.exit(1)

    # select_model: interactive picker when --model omitted and terminal is a TTY
    _preferred = getattr(args, "model", "")
    _interactive = (not _preferred) and cmd in ("chat", "agent")
    model_spec = select_model(hw, preferred=_preferred, interactive=_interactive)
    model = model_spec.ollama_tag

    if cmd == "chat":
        run_chat(hw, prov, model,
                 thinking=args.think, budget=args.budget, ws=ws)

    elif cmd == "tui":
        scaffold(ws, hw)
        subprocess.run([sys.executable, str(ws / "tui_app.py")], check=True, timeout=3600)

    elif cmd == "up":
        if _preferred: hw.model = _preferred
        run_server(ws, hw, port=args.port)

    elif cmd == "agent":
        task     = " ".join(args.task)
        soul     = load_ws_file(ws, "SOUL.md",     _DEFAULT_SOUL)
        identity = load_ws_file(ws, "IDENTITY.md", "")
        tools_md = load_ws_file(ws, "TOOLS.md",    _DEFAULT_TOOLS)
        skills   = load_skills_index(ws)
        mem      = Memory(ws, hw.tier)
        cfg      = AgentConfig(
            provider=prov, model=model, workspace=ws,
            thinking=args.think, critic=not args.no_critic,
            autonomy_level=getattr(args, "autonomy", 1))
        bridge   = SystemBridge(hw, ws)
        agent    = Agent(cfg, soul=soul, identity=identity,
                         tools_md=tools_md, skills=skills, memory=mem,
                         hw=hw, bridge=bridge)
        bridge.start()
        print(f"\n{bold('Task:')} {task}")
        print(f"{dim('Model:')} {model}  {dim('Role:')} {get_system_role().value}\n")
        result = agent.run_task(task)
        bridge.stop()
        print(f"\n{green('── Result ──')}\n{result}\n")

    elif cmd == "eval":
        print(BANNER)
        print(f"{bold('Behavioral Eval Harness')}  model={cyan(model)}\n")
        try:
            prov_e = build_provider_chain(hw)
        except BackendError:
            prov_e = None
        harness = EvalHarness(provider=prov_e, judge_model=model)
        soul     = load_ws_file(ws, "SOUL.md",     _DEFAULT_SOUL)
        identity = load_ws_file(ws, "IDENTITY.md", "")
        tools_md = load_ws_file(ws, "TOOLS.md",    _DEFAULT_TOOLS)
        mem_e    = Memory(ws, hw.tier)
        if prov_e:
            cfg_e  = AgentConfig(provider=prov_e, model=model,
                                  workspace=ws, autonomy_level=2, critic=False)
            agent_e = Agent(cfg_e, soul=soul, identity=identity,
                             tools_md=tools_md, memory=mem_e, hw=hw)
        else:
            print(red("No backend available — cannot run eval.\n"
                      "  Start Ollama and run: essence pull " + hw.model))
            sys.exit(1)
        # Filter to single scenario if specified
        scenarios = None
        scenario_name = getattr(args, "scenario", "")
        if scenario_name:
            scenarios = [s for s in harness.BUILTIN_SCENARIOS
                         if s.name == scenario_name]
            if not scenarios:
                print(red(f"Scenario '{scenario_name}' not found."))
                print(f"Available: "
                      f"{', '.join(s.name for s in harness.BUILTIN_SCENARIOS)}")
                sys.exit(1)
        results_e = harness.run(agent_e, scenarios=scenarios, verbose=True)
        n_pass = sum(1 for r in results_e if r.passed)
        n_total = len(results_e)
        score_avg = sum(r.score for r in results_e) / max(n_total, 1)
        print(f"\n  {bold('Result:')} {n_pass}/{n_total} passed  "
              f"avg_score={score_avg:.2f}")
        if getattr(args, "save_baseline", False):
            bl_path = harness.save_baseline(agent_e)
            print(f"  {green('✓')} Baseline saved → {bl_path}")
        if getattr(args, "regression", False):
            passed_regression = harness.regression_check(results_e, ws)
            if not passed_regression:
                print(red("  ✗ Regression check FAILED — score drop exceeds threshold"))
                sys.exit(2)
            else:
                print(f"  {green('✓')} Regression check passed")
        if getattr(args, "drift", False):
            print(f"\n  {bold('Drift check')} against baseline …")
            report = harness.drift_check(agent_e)
            if report["passed"]:
                print(f"  {green('✓')} No drift detected "
                      f"({len(report['scores'])} scenarios, "
                      f"avg Δ={sum(v for v in report['delta'].values() if v is not None):.3f})")
            else:
                print(red(f"  ✗ {len(report['regressions'])} regression(s) detected:"))
                for reg in report["regressions"]:
                    print(f"    {red(reg['scenario']):<40} "
                          f"baseline={reg['baseline']:.2f} → "
                          f"current={reg['current']:.2f} "
                          f"(drop={reg['drop']:.2f})")
                sys.exit(2)
        # Append eval summary to LEARNED.md for long-term continuity
        _learned_path = ws / "LEARNED.md"
        _ts = time.strftime("%Y-%m-%d %H:%M")
        _fail_names = [r.scenario for r in results_e if not r.passed]
        _learned_entry = (
            f"\n<!-- eval {_ts} -->\n"
            f"- Eval {_ts}: {n_pass}/{n_total} passed, "
            f"avg_score={score_avg:.2f}"
            + (f", failed=[{', '.join(_fail_names)}]" if _fail_names else "")
            + "\n"
        )
        try:
            with open(_learned_path, "a", encoding="utf-8") as _lf:
                _lf.write(_learned_entry)
        except Exception:
            pass

    else:
        _parser().print_help()


