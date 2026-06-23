"""Phase 4 — ChannelAdapter ABC + InboundMessage + ChannelRegistry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    """Normalised inbound message from any channel."""
    source:    str
    sender_id: str
    text:      str
    raw:       dict = field(default_factory=dict)


class ChannelAdapter(ABC):
    """Abstract base for all channel adapters.

    Subclasses must:
      * Set a unique ``NAME`` class attribute (lowercase, no spaces).
      * Implement ``available()``, ``send()``, ``poll()``.
    """
    NAME: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """Return True if the channel is configured and reachable."""
        ...

    @abstractmethod
    def send(self, text: str, target: str) -> bool:
        """Send *text* to *target* (chat_id, webhook URL, room ID, …).

        Return True on success, False on failure.
        """
        ...

    @abstractmethod
    def poll(self) -> list[InboundMessage]:
        """Return pending inbound messages (empties the queue on each call)."""
        ...


class ChannelRegistry:
    """Routes outbound messages and aggregates inbound messages across adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        self._adapters[adapter.NAME] = adapter

    def available(self) -> list[str]:
        """Return names of all adapters that are currently reachable."""
        return [n for n, a in self._adapters.items() if a.available()]

    def broadcast(self, text: str, targets: dict[str, str]) -> dict[str, bool]:
        """Send *text* via each channel named in *targets*.

        *targets* maps channel-name → target-id, e.g.
        ``{"telegram": "123456789", "slack": "#general"}``.

        Returns a dict mapping channel-name → success bool.
        """
        out: dict[str, bool] = {}
        for ch_name, target in targets.items():
            adapter = self._adapters.get(ch_name)
            out[ch_name] = adapter.send(text, target) if adapter else False
        return out

    def poll_all(self) -> list[InboundMessage]:
        """Poll every registered adapter; return all pending messages."""
        msgs: list[InboundMessage] = []
        for adapter in self._adapters.values():
            try:
                msgs.extend(adapter.poll())
            except Exception:
                pass
        return msgs

    def get(self, name: str) -> ChannelAdapter | None:
        return self._adapters.get(name)

    def __repr__(self) -> str:
        return f"ChannelRegistry(adapters={list(self._adapters)})"


_registry: ChannelRegistry | None = None


def get_channel_registry() -> ChannelRegistry:
    global _registry
    if _registry is None:
        _registry = ChannelRegistry()
    return _registry
