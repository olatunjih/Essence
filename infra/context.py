""" — request-scoped contextvars."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# REQUEST-SCOPED STATE  — contextvars for trace propagation
# ══════════════════════════════════════════════════════════════════════════════
# Propagates request context (user_id, session_id, request_id) to every
# log line, audit entry, and trace span without passing extra arguments.
# Set at the start of each FastAPI request handler via set_request_context().
# Read anywhere in the call chain via get_request_context().

import contextvars as _cv

_request_ctx: "_cv.ContextVar[dict]" = _cv.ContextVar(
    "essence_request_ctx",
    default={"user_id": "anon", "session_id": "", "request_id": ""}
)


def set_request_context(user_id: str = "anon",
                         session_id: str = "",
                         request_id: str = "") -> "_cv.Token":
    """Set request context for the current async task / thread.
    Returns a token that can be passed to reset_request_context()."""
    return _request_ctx.set({
        "user_id":    user_id,
        "session_id": session_id,
        "request_id": request_id or secrets.token_hex(8),
    })


def get_request_context() -> dict:
    """Return the current request context dict."""
    return _request_ctx.get()


def reset_request_context(token: "_cv.Token") -> None:
    """Restore previous context (call in finally block)."""
    _request_ctx.reset(token)


def ctx_log_extra(extra: dict | None = None) -> dict:
    """Merge request context into a log extra dict."""
    ctx  = _request_ctx.get()
    base = {"user_id": ctx["user_id"],
            "session": ctx["session_id"],
            "req_id":  ctx["request_id"]}
    if extra:
        base.update(extra)
    return base


# ══════════════════════════════════════════════════════════════════════════════
