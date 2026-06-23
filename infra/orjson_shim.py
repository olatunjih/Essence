""" — orjson drop-in with stdlib fallback."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# FAST JSON SERIALIZER  — orjson drop-in with stdlib fallback
# ══════════════════════════════════════════════════════════════════════════════
# orjson is 10× faster than stdlib json, handles datetime/UUID/Path natively,
# and produces smaller output. Drop-in: all json.dumps/loads calls in the
# hot path (tool dispatch, memory store, audit, NATS, HTTP responses) benefit.
# Falls back to stdlib json when orjson is not installed.

try:
    import orjson as _orjson  # type: ignore   # pip install orjson
    _ORJSON = True

    def _fast_dumps(obj: Any, **kw) -> str:
        """orjson.dumps → str (stdlib json.dumps compat)."""
        opts = _orjson.OPT_NON_STR_KEYS
        if kw.get("indent") == 2:
            opts |= _orjson.OPT_INDENT_2
        if kw.get("sort_keys"):
            opts |= _orjson.OPT_SORT_KEYS
        return _orjson.dumps(obj, default=kw.get("default"), option=opts).decode()

    def _fast_loads(s: "str | bytes") -> Any:
        return _orjson.loads(s)

except ImportError:
    _ORJSON = False
    _fast_dumps = json.dumps   # type: ignore
    _fast_loads = json.loads   # type: ignore


# Use fast serializer in the hot paths: tool dispatch, memory, audit, NATS.
# The standard json module is still used for non-performance-critical paths.

# ══════════════════════════════════════════════════════════════════════════════
