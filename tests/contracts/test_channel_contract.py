"""Phase 10.4 — Contract tests for ChannelAdapter implementations."""
from __future__ import annotations
import pytest
from essence.channels.base import ChannelAdapter, InboundMessage, ChannelRegistry


class _NullAdapter(ChannelAdapter):
    """Minimal no-op adapter for contract testing."""
    NAME = "null"

    def available(self) -> bool:
        return True

    def send(self, text: str, target: str) -> bool:
        return True

    def poll(self) -> list[InboundMessage]:
        return []


class _UnavailableAdapter(ChannelAdapter):
    NAME = "unavailable"

    def available(self) -> bool:
        return False

    def send(self, text: str, target: str) -> bool:
        return False

    def poll(self) -> list[InboundMessage]:
        return []


@pytest.fixture
def null_adapter() -> _NullAdapter:
    return _NullAdapter()


@pytest.fixture
def registry() -> ChannelRegistry:
    r = ChannelRegistry()
    r.register(_NullAdapter())
    r.register(_UnavailableAdapter())
    return r


class TestChannelAdapterContract:
    def test_available_returns_bool(self, null_adapter: _NullAdapter) -> None:
        assert isinstance(null_adapter.available(), bool)

    def test_send_returns_bool(self, null_adapter: _NullAdapter) -> None:
        assert isinstance(null_adapter.send("hello", "target123"), bool)

    def test_poll_returns_list(self, null_adapter: _NullAdapter) -> None:
        msgs = null_adapter.poll()
        assert isinstance(msgs, list)

    def test_poll_items_are_inbound_messages(self) -> None:
        m = InboundMessage(source="test", sender_id="user1", text="hi", raw={})
        assert m.source == "test"
        assert m.sender_id == "user1"
        assert m.text == "hi"


class TestChannelRegistry:
    def test_available_lists_ready_channels(self, registry: ChannelRegistry) -> None:
        avail = registry.available()
        assert "null" in avail
        assert "unavailable" not in avail

    def test_broadcast_returns_dict(self, registry: ChannelRegistry) -> None:
        result = registry.broadcast("hi", {"null": "target1"})
        assert isinstance(result, dict)
        assert result["null"] is True

    def test_broadcast_missing_channel_returns_false(self, registry: ChannelRegistry) -> None:
        result = registry.broadcast("hi", {"ghost_channel": "target"})
        assert result.get("ghost_channel") is False

    def test_poll_all_returns_list(self, registry: ChannelRegistry) -> None:
        msgs = registry.poll_all()
        assert isinstance(msgs, list)
