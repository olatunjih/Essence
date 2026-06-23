"""BUILTIN_TOOLS list +  ToolRegistry class."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.orjson_shim import _fast_dumps  # noqa: F401  [real source bug]
import urllib.parse, urllib.request, urllib.error  # noqa: F401  [real source bug: used below without explicit import]
from essence.security.sandbox import sandbox_check, get_container_sandbox, ProcessSandbox  # noqa: F401  [real source bug]

# TOOL REGISTRY  (OpenAI / MCP function-calling schema)
# ══════════════════════════════════════════════════════════════════════════════
# TOOL SCHEMAS live immediately below (BUILTIN_TOOLS list).
# TOOL IMPLEMENTATIONS follow (_tool_shell, _tool_read, etc.).
# Schema is directly compatible with:
#   • Ollama native tool calling (Ollama ≥0.5)
#   • Qwen3 MCP via Qwen-Agent wrapper
#   • Nemotron 3 Super (qwen3_coder tool-call parser)
#   • Any OpenAI-compat endpoint

BUILTIN_TOOLS: list[dict] = [
    {"type": "function", "function": {
        "name": "shell",
        "description": "Run a sandboxed shell command; returns stdout+stderr. "
                       "Use for file ops, git, system queries, package installs.",
        "parameters": {"type": "object", "required": ["command"],
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 15}}}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file from the workspace.",
        "parameters": {"type": "object", "required": ["path"],
            "properties": {
                "path":     {"type": "string"},
                "encoding": {"type": "string", "default": "utf-8"}}}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Write content to a file (creates or overwrites).",
        "parameters": {"type": "object", "required": ["path", "content"],
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "python_exec",
        "description": "Execute a Python snippet in a sandboxed subprocess.",
        "parameters": {"type": "object", "required": ["code"],
            "properties": {
                "code":    {"type": "string"},
                "timeout": {"type": "integer", "default": 10}}}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web via DuckDuckGo (no API key required).",
        "parameters": {"type": "object", "required": ["query"],
            "properties": {
                "query":       {"type": "string"},
                "max_results": {"type": "integer", "default": 5}}}}},
    {"type": "function", "function": {
        "name": "heartbeat_add",
        "description": "Schedule a recurring background task.",
        "parameters": {"type": "object",
            "required": ["name", "message", "schedule"],
            "properties": {
                "name":     {"type": "string",
                             "description": "Unique task identifier"},
                "message":  {"type": "string",
                             "description": "Task prompt to run on schedule"},
                "schedule": {"type": "string",
                             "description": "Interval: '30m', '1h', '1d'; "
                                            "or cron: 'cron:0 9 * * *'"}}}}},
    {"type": "function", "function": {
        "name": "analyze_image",
        "description": "Analyze an image with a vision-language model. "
                       "Requires hw.tier >= 1 and VRAM >= 4 GB. "
                       "Returns a text description or answer to the question.",
        "parameters": {"type": "object", "required": ["path", "question"],
            "properties": {
                "path":     {"type": "string",
                             "description": "Absolute or workspace-relative image path"},
                "question": {"type": "string",
                             "description": "Question to ask about the image"}}}}},
    {"type": "function", "function": {
        "name": "build_skill",
        "description": "Auto-build a new skill: scaffold a SKILL.md in workspace/skills/ "
                       "with the agent writing the instructions. The skill is sandboxed "
                       "to its declared capability list on first run.",
        "parameters": {"type": "object", "required": ["description"],
            "properties": {
                "description": {"type": "string",
                                "description": "Natural-language description of "
                                               "what the skill should do"}}}}},
    # ── v12: RAG / document ingestion ────────────────────────────────────────
    {"type": "function", "function": {
        "name": "ingest",
        "description": "Ingest a file, directory, or URL into the RAG memory store. "
                       "Supports PDF, DOCX, HTML, CSV, Markdown, plain text. "
                       "After ingestion, agent memory search retrieves relevant passages.",
        "parameters": {"type": "object", "required": ["path_or_url"],
            "properties": {
                "path_or_url": {"type": "string",
                                "description": "File path, directory path, or https:// URL"}}}}},
    # ── v12: data analysis & ML ───────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "run_analysis",
        "description": "Run data analysis or ML on a dataset file (CSV/parquet/JSON/Excel). "
                       "Tasks: eda, cluster, classify, regress, forecast, anomaly, "
                       "ab_test, sentiment, risk, churn, feature_importance. "
                       "Saves plots to workspace/plots/. Returns JSON metrics.",
        "parameters": {"type": "object", "required": ["dataset_path", "task"],
            "properties": {
                "dataset_path": {"type": "string"},
                "task": {"type": "string",
                         "enum": ["eda","cluster","classify","regress","forecast",
                                  "anomaly","ab_test","sentiment","risk","churn",
                                  "feature_importance","correlation","segmentation"]},
                "target_col": {"type": "string", "default": ""},
                "config": {"type": "object",
                           "description": "Task-specific options e.g. "
                           "{algorithm, n_clusters, periods, group_col, hpo, confidence}"}}}}},
    {"type": "function", "function": {
        "name": "train_model",
        "description": "End-to-end ML model training on a CSV dataset. "
                       "model_type: auto|sklearn_rf|sklearn_gbm|sklearn_lr|pytorch_mlp. "
                       "Set config.hpo=true for Optuna hyperparameter optimisation. "
                       "Saves model artifact to workspace/models/<run_id>/.",
        "parameters": {"type": "object", "required": ["dataset_path", "target_col"],
            "properties": {
                "dataset_path": {"type": "string"},
                "target_col":   {"type": "string"},
                "model_type":   {"type": "string", "default": "auto"},
                "config":       {"type": "object"}}}}},
    # ── v12: fine-tuning ──────────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "finetune",
        "description": "Fine-tune an LLM (Llama3, Qwen2.5, Mistral, Phi, Gemma2) on "
                       "a local JSONL dataset using unsloth (fastest) or PEFT LoRA. "
                       "Dataset: JSONL with {prompt, response} or Alpaca {instruction, input, output}. "
                       "Requires T2+ hardware (≥12 GB VRAM).",
        "parameters": {"type": "object", "required": ["base_model", "dataset_path"],
            "properties": {
                "base_model":   {"type": "string",
                                 "description": "HF model ID e.g. unsloth/llama-3-8b-bnb-4bit"},
                "dataset_path": {"type": "string"},
                "output_dir":   {"type": "string", "default": ""},
                "config":       {"type": "object",
                                 "description": "{epochs, lr, batch_size, lora_r, max_seq_len}"}}}}},
    # ── v12: computer vision ──────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "vision_task",
        "description": "Computer vision on an image file. "
                       "task: classify (ResNet/ViT), detect (YOLOv8), "
                       "ocr (tesseract/PaddleOCR), segment (SAM, T3 only). "
                       "Tiered: T0=OCR only, T1=detect+classify, T2=full, T3=segment.",
        "parameters": {"type": "object", "required": ["path", "task"],
            "properties": {
                "path":  {"type": "string"},
                "task":  {"type": "string",
                          "enum": ["classify", "detect", "ocr", "segment"]},
                "model": {"type": "string", "default": "auto"}}}}},
    # ── v12: speech / audio ───────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "speech",
        "description": "Speech and audio tasks. "
                       "task=transcribe: STT via faster-whisper (CPU-friendly). "
                       "task=translate: transcribe + translate to English. "
                       "task=tts: text-to-speech via kokoro-onnx (pass text as audio_path). "
                       "task=classify: audio feature extraction.",
        "parameters": {"type": "object", "required": ["audio_path"],
            "properties": {
                "audio_path": {"type": "string",
                               "description": "Audio file path, or text string when task=tts"},
                "task":     {"type": "string",
                             "enum": ["transcribe","translate","tts","classify"],
                             "default": "transcribe"},
                "language": {"type": "string", "default": "en"}}}}},
    # ── v16: Browser automation (Playwright) ─────────────────────────────────
    {"type": "function", "function": {
        "name": "browser_open",
        "description": "Navigate to a URL and return the page's main text content. "
                       "Uses Playwright headless Chromium with full JS rendering. "
                       "Falls back to urllib for plain HTML if Playwright not installed. "
                       "Opens a persistent BrowserSession — subsequent browser_click, "
                       "browser_fill, browser_extract calls operate on the same page.",
        "parameters": {"type": "object", "required": ["url"],
            "properties": {
                "url":        {"type": "string", "description": "Full URL to open"},
                "timeout_ms": {"type": "integer", "default": 15000,
                               "description": "Navigation timeout in milliseconds"}}}}},
    {"type": "function", "function": {
        "name": "browser_screenshot",
        "description": "Take a screenshot of the current browser page and return the PNG file path. "
                       "Call browser_open first. Pair with analyze_image for VLM visual reasoning. "
                       "Requires: pip install playwright && playwright install chromium",
        "parameters": {"type": "object", "required": [],
            "properties": {
                "selector": {"type": "string",
                             "description": "CSS selector for element screenshot (optional)"}}}}},
    {"type": "function", "function": {
        "name": "browser_click",
        "description": "Click an element on the current browser page by CSS selector. "
                       "Call browser_open first to establish a session.",
        "parameters": {"type": "object", "required": ["selector"],
            "properties": {
                "selector":   {"type": "string", "description": "CSS selector of element to click"},
                "timeout_ms": {"type": "integer", "default": 5000}}}}},
    {"type": "function", "function": {
        "name": "browser_fill",
        "description": "Fill an input field on the current browser page by CSS selector. "
                       "Call browser_open first to establish a session.",
        "parameters": {"type": "object", "required": ["selector", "value"],
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of input to fill"},
                "value":    {"type": "string", "description": "Text value to enter"}}}}},
    {"type": "function", "function": {
        "name": "browser_extract",
        "description": "Extract inner text from elements matching a CSS selector on the current page. "
                       "Call browser_open first to establish a session.",
        "parameters": {"type": "object", "required": ["selector"],
            "properties": {
                "selector": {"type": "string",
                             "description": "CSS selector — e.g. 'h1', '.product-price', 'table'",
                             "default": "body"}}}}},
    # ── v16: Computer use (desktop automation) ────────────────────────────────
    {"type": "function", "function": {
        "name": "computer_screenshot",
        "description": "Take a screenshot of the entire desktop. Returns PNG file path. "
                       "Requires Essence_COMPUTER_USE=1 and pip install pyautogui pillow. "
                       "Pair with analyze_image for VLM-powered GUI reasoning.",
        "parameters": {"type": "object", "required": [], "properties": {}}}},
    {"type": "function", "function": {
        "name": "computer_click",
        "description": "Click at screen coordinates (x, y). Requires Essence_COMPUTER_USE=1.",
        "parameters": {"type": "object", "required": ["x", "y"],
            "properties": {
                "x":      {"type": "integer", "description": "Screen x coordinate"},
                "y":      {"type": "integer", "description": "Screen y coordinate"},
                "button": {"type": "string", "enum": ["left","right","middle"],
                           "default": "left"}}}}},
    {"type": "function", "function": {
        "name": "computer_type",
        "description": "Type text at the current cursor position. Requires Essence_COMPUTER_USE=1.",
        "parameters": {"type": "object", "required": ["text"],
            "properties": {
                "text":     {"type": "string", "description": "Text to type"},
                "interval": {"type": "number", "default": 0.02,
                             "description": "Delay between keystrokes in seconds"}}}}},
    # ── v16: Voice pipeline ────────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "voice_transcribe",
        "description": "Transcribe an audio file to text using faster-whisper (offline STT). "
                       "Accepts .wav, .mp3, .ogg, .flac files.",
        "parameters": {"type": "object", "required": ["audio_path"],
            "properties": {
                "audio_path": {"type": "string", "description": "Path to audio file"}}}}},
    {"type": "function", "function": {
        "name": "voice_speak",
        "description": "Synthesise speech from text using kokoro-onnx TTS (offline). "
                       "Plays through default audio device.",
        "parameters": {"type": "object", "required": ["text"],
            "properties": {
                "text": {"type": "string", "description": "Text to speak aloud"}}}}},
    # ── Skill tools (lazy loading + self-authoring) ───────────────────────────
    {"type": "function", "function": {
        "name": "read_skill",
        "description": "Read the full SKILL.md instructions for a named skill. "
                       "Call this when the skills index shows a relevant skill and "
                       "you need its complete instructions before using it.",
        "parameters": {"type": "object", "required": ["skill_name"],
            "properties": {
                "skill_name": {"type": "string",
                               "description": "Exact skill name from the Available Skills index"}}}}},
    {"type": "function", "function": {
        "name": "skill_write",
        "description": "Create a new skill from scratch and hot-reload it into the active "
                       "tool registry. Use when you cannot satisfy a request with existing "
                       "tools and a reusable capability would help. Writes SKILL.md + "
                       "optional tool.py, installs requirements, validates, and activates.",
        "parameters": {"type": "object",
            "required": ["skill_name", "description", "skill_md"],
            "properties": {
                "skill_name": {"type": "string",
                               "description": "Short slug, e.g. 'pdf-summariser' (no spaces)"},
                "description": {"type": "string",
                                "description": "One-sentence description shown in the skills index"},
                "skill_md": {"type": "string",
                             "description": "Full SKILL.md content (instructions for this skill)"},
                "tool_py": {"type": "string",
                            "description": "Optional Python tool implementation code"},
                "requirements": {"type": "string",
                                 "description": "Optional newline-separated pip requirements"}}}}},
]


# ══════════════════════════════════════════════════════════════════════════════

# TOOL REGISTRY  (plug-and-play tool extension point)
# ══════════════════════════════════════════════════════════════════════════════
# Register new tools at runtime — Agent._dispatch auto-routes to them.
#
#   from essence import TOOL_REGISTRY
#   def my_handler(args: dict) -> str:
#       return f"result for {args['query']}"
#
#   TOOL_REGISTRY.register(
#       schema={
#           "type": "function", "function": {
#               "name": "my_tool",
#               "description": "Does something useful.",
#               "parameters": {"type": "object", "required": ["query"],
#                   "properties": {"query": {"type": "string"}}},
#           }
#       },
#       handler=my_handler,
#   )

class ToolRegistry:
    """
    Dynamic tool registry — fully extensible, zero hard-coded tool list.

    Builtin tools are pre-registered at module import.  External code (plugins,
    skill files, user scripts) can add tools via ``register()``.

    Agent._dispatch() calls ``get_handler(name)`` for every tool invocation,
    routing built-in tools AND dynamically registered tools through the same
    path without any change to the agent code.

    v19: In-flight deduplication — concurrent calls with the same
    (tool_name, args_hash) are coalesced: the second caller blocks until
    the first completes and then receives the same result. This prevents
    duplicate shell executions when parallel workflow steps request the
    same side-effectful tool simultaneously.
    """

    def __init__(self) -> None:
        self._handlers:  dict[str, Callable[[dict], str]] = {}
        self._schemas:   list[dict]                       = []
        # Deduplication: maps call_key → (threading.Event, result_holder)
        self._inflight:  dict[str, tuple[threading.Event, list]] = {}
        self._dedup_lock = threading.Lock()
        self._ainflight: dict[str, tuple[asyncio.Event, list]]   = {}
        self._adedup_lock = asyncio.Lock()

    def _call_key(self, name: str, args: dict) -> str:
        """Stable hash key for (tool_name, args) — used for dedup."""
        try:
            args_sig = _fast_dumps(args, sort_keys=True, default=str)
        except Exception:
            args_sig = str(args)
        return hashlib.sha256(f"{name}:{args_sig}".encode()).hexdigest()[:24]

    def call(self, name: str, args: dict) -> str:
        """
        Deduplicated synchronous tool dispatch.
        Concurrent calls with the same (name, args) are coalesced.
        Direct handler access is still available via get_handler() for
        callers that manage their own dispatch (e.g. Agent._dispatch).
        """
        handler = self._handlers.get(name)
        if handler is None:
            return f"[unknown tool: {name}]"
        key = self._call_key(name, args)
        with self._dedup_lock:
            if key in self._inflight:
                # Another coroutine is executing this exact call — wait for it.
                evt, holder = self._inflight[key]
                is_leader   = False
            else:
                evt    = threading.Event()
                holder = []
                self._inflight[key] = (evt, holder)
                is_leader = True
        if not is_leader:
            evt.wait(timeout=120)
            return holder[0] if holder else f"[dedup_timeout: {name}]"
        # Leader: execute the call and broadcast result
        try:
            result = handler(args)
        except Exception as e:
            result = f"[tool_error: {e}]"
        finally:
            with self._dedup_lock:
                holder.append(result)
                evt.set()
                self._inflight.pop(key, None)
        return result

    async def acall(self, name: str, args: dict) -> str:
        """Async version of call(). Coalesces concurrent async requests."""
        handler = self._handlers.get(name)
        if handler is None:
            return f"[unknown tool: {name}]"

        # Determine if handler is async or needs thread
        _ahandler = getattr(handler, "ahandler", None)

        key = self._call_key(name, args)
        async with self._adedup_lock:
            if key in self._ainflight:
                evt, holder = self._ainflight[key]
                is_leader   = False
            else:
                evt    = asyncio.Event()
                holder = []
                self._ainflight[key] = (evt, holder)
                is_leader = True

        if not is_leader:
            try:
                await asyncio.wait_for(evt.wait(), timeout=120)
                return holder[0] if holder else f"[dedup_timeout: {name}]"
            except asyncio.TimeoutError:
                return f"[dedup_timeout: {name}]"

        # Leader
        try:
            if _ahandler:
                result = await _ahandler(args)
            elif asyncio.iscoroutinefunction(handler):
                result = await handler(args)
            else:
                loop   = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, handler, args)
        except Exception as e:
            result = f"[tool_error: {e}]"
        finally:
            async with self._adedup_lock:
                holder.append(result)
                evt.set()
                self._ainflight.pop(key, None)
        return result

    def register(self, schema: dict, handler: Callable[[dict], str]) -> None:
        """
        Register a tool.

        ``schema`` must be an OpenAI-compat tool dict:
            {"type": "function", "function": {"name": ..., "description": ...,
             "parameters": ...}}
        ``handler`` receives the args dict and must return a str result.
        """
        name = schema["function"]["name"]
        self._handlers[name] = handler
        # Upsert: replace existing schema entry if already registered
        self._schemas = [s for s in self._schemas
                         if s["function"]["name"] != name]
        self._schemas.append(schema)
        log.debug("tool_registered", extra={"name": name})

    def get_handler(self, name: str) -> Callable[[dict], str] | None:
        return self._handlers.get(name)

    def get_tools(self) -> list[dict]:
        """Return the merged schema list (builtin + dynamic) for LLM calls."""
        return list(self._schemas)

    def names(self) -> list[str]:
        return list(self._handlers.keys())

    def unregister(self, name: str) -> None:
        self._handlers.pop(name, None)
        self._schemas = [s for s in self._schemas
                         if s["function"]["name"] != name]


TOOL_REGISTRY = ToolRegistry()


# ── Fix 5: Unified ToolRecord metadata ────────────────────────────────────────
# This dict maps each tool name from BUILTIN_TOOLS to its APDE ToolRecord
# metadata so boot_kernel() can call build_tool_records() instead of
# maintaining a parallel hand-written list.  Any tool added to BUILTIN_TOOLS
# must also appear here; boot_kernel() raises IncompleteToolRegistry at startup
# if any required tool is absent from both sources.
_TOOL_METADATA: dict[str, dict] = {
    "shell":         {"capabilities": ["exec", "read", "write"],
                      "requires_guardrails": ["G3", "G8"],
                      "cost_class": "HIGH",   "max_invocations_per_task": 20,
                      "research_only": False},
    "read_file":     {"capabilities": ["read"],
                      "requires_guardrails": [],
                      "cost_class": "LOW",    "max_invocations_per_task": 50,
                      "research_only": False},
    "write_file":    {"capabilities": ["write"],
                      "requires_guardrails": ["G3", "G7"],
                      "cost_class": "MEDIUM", "max_invocations_per_task": 20,
                      "research_only": False},
    "python_exec":   {"capabilities": ["exec"],
                      "requires_guardrails": ["G4"],
                      "cost_class": "HIGH",   "max_invocations_per_task": 10,
                      "research_only": False},
    "web_search":    {"capabilities": ["read"],
                      "requires_guardrails": [],
                      "cost_class": "LOW",    "max_invocations_per_task": 30,
                      "research_only": True},
    "heartbeat_add": {"capabilities": ["schedule"],
                      "requires_guardrails": [],
                      "cost_class": "LOW",    "max_invocations_per_task": 5,
                      "research_only": False},
    "analyze_image": {"capabilities": ["read"],
                      "requires_guardrails": [],
                      "cost_class": "LOW",    "max_invocations_per_task": 10,
                      "research_only": False},
}


def build_tool_records() -> list:
    """
    Build a list of ToolRecord objects from BUILTIN_TOOLS + _TOOL_METADATA.

    Fix 5: This is the single authoritative source for tool records used by
    boot_kernel().  Previously boot.py maintained a separate hand-written list
    that could drift from BUILTIN_TOOLS.  Now both the LLM tool-call schema
    (BUILTIN_TOOLS) and the APDE guardrail/resource metadata (_TOOL_METADATA)
    are generated from the same canonical tool name set.

    Returns:
        list[ToolRecord] for all tools in BUILTIN_TOOLS.  Tools without a
        _TOOL_METADATA entry receive safe defaults (no guardrails, LOW cost).
    """
    from essence.apde_types import ToolRecord
    records = []
    for tool_def in BUILTIN_TOOLS:
        name = tool_def["function"]["name"]
        meta = _TOOL_METADATA.get(name, {
            "capabilities": [],
            "requires_guardrails": [],
            "cost_class": "LOW",
            "max_invocations_per_task": 10,
            "research_only": False,
        })
        records.append(ToolRecord(
            tool_name=name,
            capabilities=meta["capabilities"],
            requires_guardrails=meta["requires_guardrails"],
            cost_class=meta["cost_class"],
            max_invocations_per_task=meta["max_invocations_per_task"],
            research_only=meta["research_only"],
        ))
    return records


def _tool_shell(command: str, timeout: int = 15,
                workspace: Path | None = None,
                allow_outside: bool = False) -> str:
    ws  = workspace or Path.cwd()
    # Application-level pre-filter always runs first (fast blocklist check)
    err = sandbox_check(command, ws, allow_outside)
    if err:
        return err
    # OS-level container isolation: when Essence_CONTAINER=1, route through
    # ContainerSandbox instead of the host process.  This provides kernel-enforced
    # filesystem/network/PID isolation that the blocklist alone cannot guarantee.
    cs = get_container_sandbox(ws)
    if cs.available():
        return cs.run(command, timeout=timeout)
    # v23: Fallback — ProcessSandbox (forked process + resource limits)
    # is strictly safer than SeccompSandbox (same process, only ulimit).
    try:
        argv = shlex.split(command)
    except ValueError as e:
        return f"[error: malformed command — {e}]"
    try:
        def _run_cmd():
            r = subprocess.run(argv, cwd=str(ws), timeout=timeout,
                               capture_output=True, text=True)
            return (r.stdout + r.stderr).strip() or "(no output)"
        return ProcessSandbox.run(_run_cmd, timeout=float(timeout) + 2)
    except RuntimeError as e:
        return f"[sandbox: {e}]"
    except Exception as e:
        return f"[error: {e}]"


def _tool_read(path: str, encoding: str = "utf-8") -> str:
    try:
        return Path(path).expanduser().read_text(encoding=encoding)
    except Exception as e:
        return f"[error: {e}]"


def _tool_write(path: str, content: str,
                workspace: Path | None = None) -> str:
    try:
        p = Path(path).expanduser().resolve()
        # Enforce workspace boundary — same guard as shell tool
        if workspace is not None:
            ws_resolved = workspace.resolve()
            if not str(p).startswith(str(ws_resolved)):
                return (f"[BLOCKED: write_file path outside workspace — "
                        f"{p}  (workspace: {ws_resolved})]")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} bytes → {p}"
    except Exception as e:
        return f"[error: {e}]"


def _tool_python(code: str, timeout: int = 10) -> str:
    """
    Execute Python code in an isolated subprocess with resource caps.
    Uses RestrictedPython when available for soft sandboxing; falls back to
    plain subprocess with ulimit-style limits via resource module on POSIX.
    """
    def _preexec():
        try:
            import resource  # type: ignore
            # 256 MB address space, 30 s CPU, 64 MB file write
            resource.setrlimit(resource.RLIMIT_AS,  (1024*1024*1024, 1024*1024*1024))
            resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
            resource.setrlimit(resource.RLIMIT_FSIZE, (64*1024*1024, 64*1024*1024))
        except Exception:
            pass
    # Prepend a shim that blocks the most dangerous stdlib imports.
    # This is a soft barrier — it prevents casual misuse, not determined attacks.
    _BLOCKED_IMPORTS = (
        "import socket", "import subprocess", "import multiprocessing",
        "import ctypes", "import pty", "import fcntl",
        "from socket", "from subprocess", "from multiprocessing",
        "from ctypes", "import importlib", "import os", "import signal",
        "import shutil", "import tempfile", "from importlib", "from os",
    )
    # Single source of truth: derive the runtime blocklist from the
    # human-readable _BLOCKED_IMPORTS list above (was previously a
    # separately hardcoded, easy-to-drift duplicate).
    _BLOCKED_NAMES = frozenset(
        s.split()[1].split(".")[0] for s in _BLOCKED_IMPORTS if len(s.split()) >= 2
    )
    _shim = (
        "import builtins as _bi, sys as _sys\n"
        "_orig_import = _bi.__import__\n"
        f"_BLOCKED = {repr(_BLOCKED_NAMES)}\n"
        "def _safe_import(name, *a, _oi=_orig_import, **k):\n"
        "    if name.split('.')[0] in _BLOCKED:\n"
        "        raise ImportError(f'[sandbox] import {name!r} is not allowed in python_exec')\n"
        "    return _oi(name, *a, **k)\n"
        "_bi.__import__ = _safe_import\n"
        "del _bi\n"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", _shim + code],
            capture_output=True, text=True, timeout=timeout,
            preexec_fn=_preexec if platform.system() != "Windows" else None,
        )
        return (r.stdout + r.stderr).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "[timeout]"
    except Exception as e:
        return f"[error: {e}]"


def _tool_search(query: str, max_results: int = 5) -> str:
    """
      SEARCH CASCADE  (production-grade, graceful fallback)

    Priority order (stops at first non-empty result):
      0. Tavily API     — TAVILY_API_KEY  (1k free/month, best quality)
      1. Brave API      — BRAVE_API_KEY   (2k free/month, full web)
      2. SearXNG        — SEARXNG_URL     (self-hosted, privacy-first)
      3. Jina Reader    — parse first DDG HTML result URL, fetch full page
      4. DDG Instant    — zero-dependency last resort

    Tavily and Brave are the recommended production paths.
    The Jina/DDG path is a brittle scrape — works until DDG changes layout.
    Set TAVILY_API_KEY for reliable production search.
    """
    q = urllib.parse.quote_plus(query)

    # ── 0. Tavily API (production path — most reliable) ───────────────────
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    if tavily_key:
        try:
            payload = json.dumps({
                "api_key": tavily_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            }).encode()
            req = urllib.request.Request(
                "https://api.tavily.com/search",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST")
            if _HTTPX_POOL and get_sync_client():
                # Tavily requires POST with JSON body; was incorrectly
                # issuing a bodiless GET, discarding the api_key/query payload.
                _tav_payload = {
                    "api_key": tavily_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                }
                _hresp = get_sync_client().post(
                    "https://api.tavily.com/search",
                    json=_tav_payload,
                    timeout=8)
                raw = _hresp.json()
            else:
                raw  = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
            hits = [f"{r.get('title','')}: {r.get('content','')}"
                    for r in raw.get("results", [])[:max_results]]
            result = "\n".join(h for h in hits if h).strip()
            if result:
                return result
        except Exception as _e:
            log.debug("search_tavily_error", extra={"error": str(_e)[:120]})

    # ── 1. Brave Search API (2k free calls/month) ─────────────────────────
    brave_key = os.environ.get("BRAVE_API_KEY", "")
    if brave_key:
        try:
            req = urllib.request.Request(
                f"https://api.search.brave.com/res/v1/web/search?q={q}&count={max_results}",
                headers={"Accept": "application/json",
                         "X-Subscription-Token": brave_key})
            raw  = json.loads(urllib.request.urlopen(req, timeout=8).read().decode("utf-8", errors="replace"))
            hits = [f"{r.get('title','')}: {r.get('description','')}"
                    for r in raw.get("web", {}).get("results", [])[:max_results]]
            result = "\n".join(h for h in hits if h).strip()
            if result:
                return result
        except Exception as _e:
            log.debug("search_brave_error", extra={"error": str(_e)[:120]})

    # ── 2. SearXNG (self-hosted, privacy-respecting) ───────────────────────
    searxng_url = os.environ.get("SEARXNG_URL", "")
    if searxng_url:
        try:
            url = f"{searxng_url.rstrip('/')}/search?q={q}&format=json"
            raw  = json.loads(urllib.request.urlopen(url, timeout=8).read().decode("utf-8", errors="replace"))
            hits = [f"{r.get('title','')}: {r.get('content','')}"
                    for r in raw.get("results", [])[:max_results]]
            result = "\n".join(h for h in hits if h).strip()
            if result:
                return result
        except Exception as _e:
            log.debug("search_searxng_error", extra={"error": str(_e)[:120]})

    # ── 3. Jina Reader (fetch full page text via DDG HTML first-result URL) ─
    # NOTE: DDG HTML layout changes periodically. Multiple URL patterns tried.
    # This path is NOT production-reliable — use Tavily or Brave in production.
    try:
        _ddg_req = urllib.request.Request(
            f"https://html.duckduckgo.com/html/?q={q}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; Essence/16)"},
        )
        if _HTTPX_POOL and get_sync_client():
            ddg_html = get_sync_client().post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "kl": "us-en"}, timeout=5).text
        else:
            ddg_html = urllib.request.urlopen(_ddg_req, timeout=5).read().decode("utf-8", errors="replace")
        _url_patterns = [
            r'uddg=([^&"\s]+)',
            r'href="//duckduckgo\.com/l/\?uddg=([^&"]+)',
            r'data-href="(https?://[^"]+)"',
            r'class="[^"]*result__url[^"]*"[^>]*>\s*(https?://[^<\s]+)',
        ]
        url_match = None
        for _pat in _url_patterns:
            url_match = re.search(_pat, ddg_html)
            if url_match:
                break
        if url_match:
            target   = urllib.parse.unquote(url_match.group(1))
            jina_url = "https://reader.jina.ai/" + urllib.parse.quote(target, safe=":/?=&%")
            if _HTTPX_POOL and get_sync_client():
                page_text = get_sync_client().get(jina_url, timeout=10).text
        else:
            page_text = urllib.request.urlopen(jina_url, timeout=10).read().decode(
                "utf-8", errors="replace")
            snippet = page_text[:2000].strip()
            if snippet:
                return snippet
    except Exception as _e:
        log.debug("search_jina_ddg_error", extra={"error": str(_e)[:120]})

    # ── 4. DDG Instant Answers (zero-dependency last resort) ─────────────
    try:
        url = (f"https://api.duckduckgo.com/?q={q}"
               f"&format=json&no_html=1&skip_disambig=1")
        raw  = json.loads(urllib.request.urlopen(url, timeout=8).read().decode("utf-8", errors="replace"))
        hits = [raw.get("Abstract", "")] + [
            t.get("Text", "") for t in raw.get("RelatedTopics", [])[:max_results]]
        result = "\n".join(h for h in hits if h).strip()
        return result if result else "No results."
    except Exception as e:
        return f"[search error: {e}]"




def _tool_analyze_image(path: str, question: str,
                        hw: "HardwareProfile | None" = None) -> str:
    """
    VLM tool: analyze an image file and answer a question about it.
    Routes to qwen2.5-vl-2b (T1, ≥4 GB VRAM) or qwen2.5-vl-7b (T2, ≥8 GB).
    Falls back to a descriptive error when VLM is not available.
    """
    if hw is None or hw.tier < 1:
        return ("[analyze_image] Not available on T0 IoT tier — "
                "requires T1+ with ≥4 GB VRAM.")
    if hw.effective_gb < 4.0:
        return ("[analyze_image] Insufficient VRAM — "
                f"{hw.effective_gb:.0f} GB available, 4 GB required.")
    img_path = Path(path).expanduser()
    if not img_path.exists():
        return f"[analyze_image] File not found: {path}"

    vlm_tag = "qwen2.5-vl:7b" if hw.effective_gb >= 8.0 else "qwen2.5-vl:2b"
    try:
        import base64
        img_b64 = base64.b64encode(img_path.read_bytes()).decode()
        payload = {
            "model": vlm_tag,
            "messages": [{"role": "user", "content": question,
                          "images": [img_b64]}],
            "stream": False,
        }
        req = urllib.request.Request(
            f"{_OLLAMA_HOST}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST")
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8", errors="replace"))
        return resp.get("message", {}).get("content", "[no response]")
    except Exception as e:
        return f"[analyze_image error: {e}]"


# ══════════════════════════════════════════════════════════════════════════════
