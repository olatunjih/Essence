"""
Layer 4 — EventBus: internal publish/subscribe decoupling.

Uses fnmatch-based pattern routing so subscribers can match wildcards
like "skill.*.done" or "system.*".

Usage:
    bus = EventBus()
    await bus.subscribe("skill.*.done", my_handler)
    await bus.publish("skill.fetch_data.done", {"result": ...})
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
from typing import Any, Callable, Awaitable

log = logging.getLogger("essence.routing.event_bus")


# Handler type: async callable receiving (event_name, data)
Handler = Callable[[str, Any], Awaitable[None]]


class EventBus:
    """
    Async publish/subscribe event bus with fnmatch pattern matching.

    Thread-safe via asyncio.Lock.
    Handlers are called concurrently via asyncio.gather.
    """

    def __init__(self) -> None:
        self._subscriptions: list[tuple[str, Handler]] = []
        self._lock = asyncio.Lock()

    async def subscribe(self, pattern: str, handler: Handler) -> None:
        """Subscribe a handler to all events matching the fnmatch pattern."""
        async with self._lock:
            self._subscriptions.append((pattern, handler))
        log.debug("event_bus_subscribe", extra={"pattern": pattern})

    def subscribe_sync(self, pattern: str, handler: Handler) -> None:
        """Synchronous variant for use during boot (before event loop is running)."""
        self._subscriptions.append((pattern, handler))
        log.debug("event_bus_subscribe_sync", extra={"pattern": pattern})

    async def publish(self, event: str, data: Any = None) -> int:
        """
        Publish an event to all matching subscribers.
        Returns the number of handlers invoked.
        """
        async with self._lock:
            matched = [
                handler
                for pattern, handler in self._subscriptions
                if fnmatch.fnmatch(event, pattern)
            ]

        if not matched:
            log.debug("event_bus_no_subscribers", extra={"event": event})
            return 0

        log.debug("event_bus_publish",
                  extra={"event": event, "handlers": len(matched)})

        results = await asyncio.gather(
            *[self._call_handler(h, event, data) for h in matched],
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            for err in errors:
                log.warning("event_bus_handler_error",
                            extra={"event": event, "error": str(err)[:120]})
        return len(matched)

    async def _call_handler(self, handler: Handler,
                            event: str, data: Any) -> None:
        try:
            await handler(event, data)
        except Exception as exc:
            log.warning("event_bus_handler_exception",
                        extra={"event": event, "error": str(exc)[:120]},
                        exc_info=True)
            raise

    def publish_sync(self, event: str, data: Any = None) -> int:
        """Synchronous publish for non-async callers (Kernel.tick, PipelineExecutor).

        Bug 6 fix: EventBus.publish() is async; calling it without await silently
        drops all events.  This method safely bridges sync callers.
        """
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event, data))
            return 0
        except RuntimeError:
            return asyncio.run(self.publish(event, data))

    async def unsubscribe(self, pattern: str, handler: Handler | None = None) -> int:
        """
        Remove subscriptions matching the pattern (and optionally handler).
        Returns the count removed.
        """
        async with self._lock:
            before = len(self._subscriptions)
            if handler is not None:
                self._subscriptions = [
                    (p, h) for p, h in self._subscriptions
                    if not (p == pattern and h is handler)
                ]
            else:
                self._subscriptions = [
                    (p, h) for p, h in self._subscriptions if p != pattern
                ]
            return before - len(self._subscriptions)


# Module-level singleton used by PipelineExecutor and ProactiveEngine
_global_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the global EventBus instance, creating it if needed."""
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus


def set_event_bus(bus: EventBus) -> None:
    """Inject a custom EventBus (for testing or kernel bootstrap)."""
    global _global_bus
    _global_bus = bus
