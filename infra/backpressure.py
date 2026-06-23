""" — streaming flow control."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# STREAMING FLOW CONTROL  — producer/consumer rate matching
# ══════════════════════════════════════════════════════════════════════════════
# Prevents OOM when LLM generates faster than WebSocket client consumes.
# BoundedTokenQueue wraps asyncio.Queue with configurable maxsize.
# When full: oldest token is discarded and a drop counter incremented.
# Exposes drop_count for observability via /api/status.

class BoundedTokenQueue:
    """
    Bounded async queue for token streaming with backpressure.
    Producer uses put_nowait(); when full, oldest token is dropped.
    Consumer uses get().
    """

    def __init__(self, maxsize: int = 200) -> None:
        self._q       = asyncio.Queue(maxsize=maxsize)
        self.drops    = 0
        self.produced = 0
        self.consumed = 0

    def put_nowait(self, item: Any) -> None:
        self.produced += 1
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            # Drop the oldest item and insert new one
            try:
                self._q.get_nowait()
                self.drops += 1
            except asyncio.QueueEmpty:
                pass
            try:
                self._q.put_nowait(item)
            except asyncio.QueueFull:
                self.drops += 1

    async def get(self) -> Any:
        item = await self._q.get()
        self.consumed += 1
        return item

    def empty(self) -> bool:
        return self._q.empty()

    def qsize(self) -> int:
        return self._q.qsize()


# ══════════════════════════════════════════════════════════════════════════════
