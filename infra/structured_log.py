""" typed events +  structlog setup."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.context import get_request_context  # noqa: F401  [real source bug: used in log_event() without import]

# TYPED LOG EVENTS  — schema-validated log records
# ══════════════════════════════════════════════════════════════════════════════
# Defines typed dataclasses for all log event types.
# log_event() validates schema and emits structured JSON.
# The DuckDB analytics queries know the exact field names.
# Falls back to plain log.info when the event model is unknown.
#
# Usage:
#   log_event(ToolCallEvent(tool="shell", session_id=sid, latency_ms=42.0))
#   log_event(LLMCallEvent(model="qwen3:8b", tokens_in=100, tokens_out=50))

@_dc.dataclass
class ToolCallEvent:
    tool:        str
    session_id:  str   = ""
    latency_ms:  float = 0.0
    success:     bool  = True
    result_len:  int   = 0
    user_id:     str   = ""

@_dc.dataclass
class LLMCallEvent:
    model:       str
    tokens_in:   int   = 0
    tokens_out:  int   = 0
    latency_ms:  float = 0.0
    thinking:    bool  = False
    session_id:  str   = ""

@_dc.dataclass
class WorkflowStepEvent:
    task_id:     str
    step_id:     int
    action:      str
    status:      str
    latency_ms:  float = 0.0
    retries:     int   = 0

@_dc.dataclass
class MemoryWriteEvent:
    layer:       str   = "working"
    key:         str   = ""
    session_id:  str   = ""
    bytes_len:   int   = 0

_EVENT_LEVEL = {
    ToolCallEvent:     "info",
    LLMCallEvent:      "info",
    WorkflowStepEvent: "info",
    MemoryWriteEvent:  "debug",
}


def log_event(event: Any) -> None:
    """
    Emit a typed log event. Merges request context automatically.
    Falls back to log.debug for unknown event types.
    """
    ctx     = get_request_context()
    level   = _EVENT_LEVEL.get(type(event), "debug")
    extra   = _dc.asdict(event) if _dc.is_dataclass(event) else {"raw": str(event)}
    extra.update({"user_id": ctx["user_id"], "req_id": ctx["request_id"]})
    name    = type(event).__name__
    getattr(log, level)(name, extra=extra)


# ══════════════════════════════════════════════════════════════════════════════

# STRUCTURED LOGGING SETUP  — structlog integration
# ══════════════════════════════════════════════════════════════════════════════
# Replaces stdlib logging + pythonjsonlogger with structlog.
# Provides: bound loggers, contextvars auto-merge, JSON prod / colored dev.
# Falls back to existing stdlib logger when structlog is not installed.
#
# ENV:  Essence_STRUCTLOG=1   Enable structlog (default: off, requires pip install structlog)

_STRUCTLOG_ENABLED = os.environ.get("Essence_STRUCTLOG", "0") == "1"

try:
    import structlog as _structlog  # type: ignore   # pip install structlog
    _STRUCTLOG = True
except ImportError:
    _structlog = None  # type: ignore
    _STRUCTLOG = False


def _setup_structlog() -> Any:
    """
    Configure structlog with contextvars merge + JSON/colored rendering.
    Returns a structlog BoundLogger if available, else the existing stdlib logger.
    """
    if not (_STRUCTLOG_ENABLED and _STRUCTLOG):
        return None

    import sys as _sys
    _structlog.configure(
        processors=[
            _structlog.contextvars.merge_contextvars,
            _structlog.stdlib.filter_by_level,
            _structlog.processors.TimeStamper(fmt="iso"),
            _structlog.stdlib.add_logger_name,
            _structlog.stdlib.add_log_level,
            _structlog.processors.StackInfoRenderer(),
            (
                _structlog.dev.ConsoleRenderer()
                if _sys.stderr.isatty()
                else _structlog.processors.JSONRenderer()
            ),
        ],
        wrapper_class=_structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=_structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    return _structlog.get_logger("essence")


def maybe_upgrade_logger() -> None:
    """Replace module-level `log` with structlog bound logger if configured."""
    global log
    sl = _setup_structlog()
    if sl is not None:
        log = sl
        log.info("structlog_activated")


# ══════════════════════════════════════════════════════════════════════════════
