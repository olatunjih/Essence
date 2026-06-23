"""
SSEStreamManager — per-session Server-Sent Events queues.

Allows skill execution to push partial results to the UI as they arrive,
enabling progressive rendering (e.g. show win_probability gauge as soon as
the prediction task completes, before the explanation task finishes).

Usage:
    manager = SSEStreamManager()
    await manager.emit(session_id, {"type": "skill_result", "data": ...})
    # In FastAPI route:
    return StreamingResponse(manager.stream(request, session_id), ...)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

log = logging.getLogger("essence.server.sse_manager")

_KEEPALIVE_INTERVAL = 15.0   # seconds between keepalive pings


class SSEStreamManager:
    """
    Per-session SSE queue manager.

    Sessions are created on first emit and cleaned up on stream completion
    or disconnect.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}

    def _get_or_create(self, session_id: str) -> asyncio.Queue:
        if session_id not in self._queues:
            self._queues[session_id] = asyncio.Queue(maxsize=256)
        return self._queues[session_id]

    async def emit(self, session_id: str, event: Any) -> None:
        """
        Push an event to the session's SSE queue.
        If the queue is full, the oldest event is discarded to prevent backpressure.
        """
        q = self._get_or_create(session_id)
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await q.put(event)

    def emit_sync(self, session_id: str, event: Any) -> None:
        """Thread-safe synchronous emit for use from executor threads."""
        try:
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(self.emit(session_id, event), loop)
        except Exception as exc:
            log.debug("sse_emit_sync_error", extra={"error": str(exc)[:80]})

    async def stream(self,
                     request: Any,
                     session_id: str) -> AsyncIterator[str]:
        """
        Async generator that yields SSE-formatted strings for a session.

        Sends keepalive comments every KEEPALIVE_INTERVAL seconds.
        Exits cleanly when the client disconnects.
        """
        q = self._get_or_create(session_id)

        try:
            while True:
                # Check client disconnect (FastAPI request.is_disconnected)
                if hasattr(request, "is_disconnected"):
                    try:
                        if await request.is_disconnected():
                            break
                    except Exception:
                        break

                try:
                    event = await asyncio.wait_for(q.get(), timeout=_KEEPALIVE_INTERVAL)
                    if event is None:   # sentinel — stream closed
                        break
                    payload = json.dumps(event) if not isinstance(event, str) else event
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield f": keepalive {int(time.time())}\n\n"
        finally:
            self._cleanup(session_id)

    def close(self, session_id: str) -> None:
        """Signal the stream to close cleanly."""
        q = self._queues.get(session_id)
        if q is not None:
            try:
                q.put_nowait(None)  # sentinel
            except asyncio.QueueFull:
                pass

    def _cleanup(self, session_id: str) -> None:
        self._queues.pop(session_id, None)
        log.debug("sse_session_cleaned", extra={"session_id": session_id})

    def active_sessions(self) -> list[str]:
        return list(self._queues.keys())


# Module-level singleton
_sse_manager: SSEStreamManager | None = None


def get_sse_manager() -> SSEStreamManager:
    global _sse_manager
    if _sse_manager is None:
        _sse_manager = SSEStreamManager()
    return _sse_manager
