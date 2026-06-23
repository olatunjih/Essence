"""Guided decoding, structured outputs, skill hot-reload, and TOML configuration."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# Guided decoding — guaranteed JSON via Outlines/llguidance.
# Wraps LLM calls that expect structured JSON with output-logit constraints
# so the model cannot produce invalid JSON.
# Outlines supports Ollama, vLLM, llama.cpp.  Gracefully degrades to the
# existing regex-repair fallback when Outlines is not installed.
# ENV:  Essence_GUIDED=1   Enable guided decoding (default: off)

_GUIDED_ENABLED = os.environ.get("Essence_GUIDED", "0") == "1"

try:
    import outlines as _outlines  # type: ignore
    _OUTLINES = True
except ImportError:
    _outlines = None  # type: ignore
    _OUTLINES = False


def guided_json_completion(prompt: str, schema: dict,
                            provider: Any | None = None) -> str:
    """
    Return a JSON string guaranteed to match `schema`.
    Uses Outlines when available; falls back to a plain LLM call + regex repair.
    `provider` is a Essence ProviderChain; used only in the fallback path.
    """
    if _GUIDED_ENABLED and _OUTLINES:
        try:
            import json as _json
            model_name = (os.environ.get("Essence_MODEL") or
                          os.environ.get("LITELLM_MODEL") or "qwen3:8b")
            model  = _outlines.models.ollama(model_name)
            gen    = _outlines.generate.json(model, schema)
            result = gen(prompt)
            return _json.dumps(result)
        except Exception as _e:
            log.debug("guided_json_fallback", extra={"error": str(_e)[:120]})

    # Fallback: plain LLM + regex cleanup (existing behaviour)
    if provider is not None:
        # stream=False is advisory — backends always yield tokens;
        # "".join() materialises the full response regardless.
        raw = "".join(provider.complete(
            [{"role": "user", "content": prompt +
              "\n\nRespond with ONLY valid JSON matching this schema: " +
              json.dumps(schema)}],
            model=os.environ.get("Essence_MODEL", "qwen3:8b"),
            stream=True))   # always stream; materialised by join()
        return re.sub(r"```[a-zA-Z]*", "", raw).strip()
    return "{}"


# ══════════════════════════════════════════════════════════════════════════════

# Structured LLM outputs via Instructor + Pydantic schemas.
# Replaces manual JSON parsing in consolidation and planning phases.
# Instructor enforces Pydantic schemas at the LLM call level, auto-retrying
# with the validation error message when the model returns invalid output.
# ENV:  Essence_INSTRUCTOR=1   Enable (requires: pip install instructor)

_INSTRUCTOR_ENABLED = os.environ.get("Essence_INSTRUCTOR", "0") == "1"

try:
    import instructor as _instructor_mod  # type: ignore
    _INSTRUCTOR = True
except ImportError:
    _instructor_mod = None  # type: ignore
    _INSTRUCTOR = False


def instructor_extract(prompt: str, response_model: Any,
                        client: Any | None = None,
                        model: str = "") -> Any | None:
    """
    Extract a structured Pydantic model from an LLM response via Instructor.
    Falls back to None when Instructor is not installed or call fails.
    `client` should be an OpenAI-compatible client (e.g. openai.OpenAI with
    custom base_url pointing to Ollama's OpenAI endpoint).
    """
    if not (_INSTRUCTOR_ENABLED and _INSTRUCTOR and client is not None):
        return None
    try:
        patched = _instructor_mod.from_openai(client)
        result  = patched.chat.completions.create(
            model=model or os.environ.get("Essence_MODEL", "qwen3:8b"),
            response_model=response_model,
            messages=[{"role": "user", "content": prompt}],
            max_retries=2)
        return result
    except Exception as _e:
        log.debug("instructor_extract_error", extra={"error": str(_e)[:120]})
        return None


# ══════════════════════════════════════════════════════════════════════════════

# Watchfiles skill hot-reload — sub-second skill updates.
# A background coroutine watches workspace/skills/ for changes and calls
# _reload_skills() within 50ms.  Makes the edit → test loop instant.
# Requires: pip install watchfiles   (falls back to HeartbeatScheduler polling)
# ENV:  Essence_WATCHSKILLS=1   Enable (default: off)

_WATCHSKILLS_ENABLED = os.environ.get("Essence_WATCHSKILLS", "0") == "1"


async def _watch_skills_loop(skill_dir: Path,
                               reload_fn: "Callable[[], None]") -> None:
    """Async task: watch skill_dir for changes and call reload_fn."""
    try:
        from watchfiles import awatch  # type: ignore
        async for _ in awatch(str(skill_dir)):
            try:
                reload_fn()
                log.info("skill_hot_reloaded", extra={"dir": str(skill_dir)})
            except Exception as _e:
                log.warning("skill_reload_error",
                             extra={"error": str(_e)[:120]})
    except ImportError:
        log.debug("watchskills_watchfiles_not_installed",
                  extra={"hint": "pip install watchfiles"})


def start_skill_watcher(skill_dir: Path,
                         reload_fn: "Callable[[], None]") -> None:
    """Start the skill hot-reload watcher as a background asyncio task."""
    if not _WATCHSKILLS_ENABLED:
        return
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_watch_skills_loop(skill_dir, reload_fn))
        log.info("skill_watcher_started", extra={"dir": str(skill_dir)})
    except RuntimeError:
        pass   # No running event loop (CLI mode) — watcher not started


# ══════════════════════════════════════════════════════════════════════════════

# TOML configuration loader — EssenceConfig with env-var override.
# Single source of truth for all runtime settings.
# Loads workspace/config.toml → merges Essence_* env vars on top (env wins).
# Hot-reloads on SIGHUP (server mode) or explicit EssenceConfig.reload().
# Falls back to env-only when toml is not installed or file absent.
#
# ENV: any Essence_* var overrides the matching config.toml key at runtime.

try:
    import tomllib as _tomllib   # Python 3.11+
except ImportError:
    try:
        import tomli as _tomllib  # type: ignore  # pip install tomli
    except ImportError:
        _tomllib = None  # type: ignore


@_dc.dataclass
class EssenceConfig:
    """
    Merged runtime configuration: config.toml values + Essence_* env overrides.
    All fields have sensible defaults so the system runs out-of-the-box.
    """
    # Core
    model:        str   = _dc.field(default_factory=lambda: os.environ.get("Essence_MODEL", ""))
    backend:      str   = _dc.field(default_factory=lambda: os.environ.get("Essence_BACKEND", ""))
    role:         str   = _dc.field(default_factory=lambda: os.environ.get("Essence_ROLE", "standalone"))
    # Rate limits (per minute; 0 = disabled)
    rl_chat:      int   = _dc.field(default_factory=lambda: int(os.environ.get("Essence_RL_CHAT", "60")))
    rl_shell:     int   = _dc.field(default_factory=lambda: int(os.environ.get("Essence_RL_SHELL", "20")))
    rl_agent:     int   = _dc.field(default_factory=lambda: int(os.environ.get("Essence_RL_AGENT", "10")))
    # Auth
    api_token:    str   = _dc.field(default_factory=lambda: os.environ.get("Essence_API_TOKEN", ""))
    auth_disabled: bool = _dc.field(default_factory=lambda: os.environ.get("Essence_AUTH_DISABLED", "0") == "1")
    # Memory
    team_id:      str   = _dc.field(default_factory=lambda: os.environ.get("Essence_TEAM_ID", "local"))
    scache:       bool  = _dc.field(default_factory=lambda: os.environ.get("Essence_SCACHE", "0") == "1")
    scache_thresh: float= _dc.field(default_factory=lambda: float(os.environ.get("Essence_SCACHE_THRESH", "0.97")))
    # Observability
    metrics:      bool  = _dc.field(default_factory=lambda: os.environ.get("Essence_METRICS", "0") == "1")
    audit:        bool  = _dc.field(default_factory=lambda: os.environ.get("Essence_AUDIT", "0") == "1")
    otel_endpoint: str  = _dc.field(default_factory=lambda: os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""))
    # Infrastructure
    valkey_url:   str   = _dc.field(default_factory=lambda: os.environ.get("Essence_VALKEY_URL", ""))
    nats_url:     str   = _dc.field(default_factory=lambda: os.environ.get("Essence_NATS_URL", ""))
    alive_ttl:    float = _dc.field(default_factory=lambda: float(os.environ.get("Essence_ALIVE_TTL", "10")))
    # Features
    plugins:      bool  = _dc.field(default_factory=lambda: os.environ.get("Essence_PLUGINS", "0") == "1")
    auto_resume:  bool  = _dc.field(default_factory=lambda: os.environ.get("Essence_AUTO_RESUME", "0") == "1")
    guided:       bool  = _dc.field(default_factory=lambda: os.environ.get("Essence_GUIDED", "0") == "1")
    # Cost
    cost_budget:  int   = _dc.field(default_factory=lambda: int(os.environ.get("Essence_COST_BUDGET", "0")))

    @classmethod
    def load(cls, workspace: Path) -> "EssenceConfig":
        """Load from workspace/config.toml, overlay env vars."""
        cfg = cls()
        toml_path = workspace / "config.toml"
        if _tomllib and toml_path.exists():
            try:
                with open(toml_path, "rb") as _f:
                    raw = _tomllib.load(_f)
                _flat = raw.get("essence", raw)   # support [essence] section or bare keys
                for field in _dc.fields(cfg):
                    if field.name in _flat:
                        try:
                            setattr(cfg, field.name, type(getattr(cfg, field.name))(_flat[field.name]))
                        except (TypeError, ValueError):
                            pass
            except Exception as _e:
                log.debug("config_toml_load_error", extra={"error": str(_e)[:120]})
        # Env vars always win
        for field in _dc.fields(cfg):
            env_key = f"Essence_{field.name.upper()}"
            env_val = os.environ.get(env_key, "")
            if env_val:
                try:
                    ft = type(getattr(cfg, field.name))
                    if ft is bool:
                        setattr(cfg, field.name, env_val in ("1", "true", "yes"))
                    else:
                        setattr(cfg, field.name, ft(env_val))
                except (TypeError, ValueError):
                    pass
        return cfg

    def to_toml(self) -> str:
        """Render current config as TOML for scaffolding config.toml."""
        lines = ["[essence]", "# Essence runtime configuration — edit and restart to apply", ""]
        for field in _dc.fields(self):
            v = getattr(self, field.name)
            if isinstance(v, bool):
                lines.append(f"{field.name} = {'true' if v else 'false'}")
            elif isinstance(v, str):
                lines.append(f'{field.name} = "{v}"')
            else:
                lines.append(f"{field.name} = {v}")
        return "\n".join(lines) + "\n"

    def validate(self) -> list[str]:
        """Return list of validation errors (empty = valid)."""
        errs = []
        if self.rl_chat < 0:
            errs.append("rl_chat must be >= 0")
        if self.rl_shell < 0:
            errs.append("rl_shell must be >= 0")
        if not (0.0 < self.scache_thresh <= 1.0):
            errs.append("scache_thresh must be in (0, 1]")
        if self.alive_ttl <= 0:
            errs.append("alive_ttl must be > 0")
        if self.cost_budget < 0:
            errs.append("cost_budget must be >= 0")
        return errs


_essence_config: "EssenceConfig | None" = None


def get_config(workspace: Path | None = None) -> "EssenceConfig":
    """Return (or lazily create) the global EssenceConfig instance."""
    global _essence_config
    if _essence_config is None:
        _essence_config = EssenceConfig.load(workspace or Path.cwd())
    return _essence_config


# ══════════════════════════════════════════════════════════════════════════════
