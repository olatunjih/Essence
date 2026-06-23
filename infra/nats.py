""" — JetStream event bus."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# — optional NATS event-bus integration (no-op without a server).
# [This module was empty — a content-loss gap from the build_pkg.py split.
#  Implemented fresh against tests/test_infra.py's contract: NATSEventBus is
#  always safe to construct/emit on even with no server reachable, and
#  get_nats_bus() returns None unless Essence_NATS_URL is set.]

try:
    import nats as _nats_lib  # type: ignore
    _NATS_AVAILABLE = True
except Exception:
    _nats_lib = None
    _NATS_AVAILABLE = False


class NATSEventBus:
    """Thin wrapper around the optional `nats-py` client. All methods are
    best-effort no-ops when the `nats` package isn't installed or the
    server isn't reachable — callers should never need to guard calls."""

    def __init__(self, url: str):
        self.url     = url
        self._ready  = False
        self._nc     = None
        if _NATS_AVAILABLE:
            try:
                import asyncio as _asyncio
                self._nc = _nats_lib.NATS()
                _asyncio.get_event_loop().run_until_complete(
                    self._nc.connect(servers=[self.url], connect_timeout=2))
                self._ready = True
            except Exception as e:
                log.debug("nats_connect_failed", extra={"url": self.url, "error": str(e)})

    def emit(self, subject: str, data: dict) -> None:
        """Publish an event; silently does nothing if not connected."""
        if not self._ready or self._nc is None:
            return
        try:
            import asyncio as _asyncio, json as _json
            _asyncio.get_event_loop().run_until_complete(
                self._nc.publish(subject, _json.dumps(data).encode("utf-8")))
        except Exception as e:
            log.debug("nats_emit_failed", extra={"subject": subject, "error": str(e)})


_nats_bus: "NATSEventBus | None" = None


def get_nats_bus() -> "NATSEventBus | None":
    """Return the process-wide NATSEventBus singleton, or None when
    Essence_NATS_URL isn't configured (the common, fully-supported case)."""
    global _nats_bus
    url = os.environ.get("Essence_NATS_URL", "")
    if not url:
        return None
    if _nats_bus is None:
        _nats_bus = NATSEventBus(url)
    return _nats_bus



