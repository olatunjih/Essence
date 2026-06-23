"""
Layer 3 — ProtocolRouter: scheme-to-transport registry.

Supports: http/https (REST), ws (WebSocket), a2a, mcp,
          telegram, slack, discord.

Usage:
    router = ProtocolRouter()
    router.register("http", RESTTransport())
    await router.send("http://api.example.com/endpoint", {"key": "val"})
"""
from __future__ import annotations

import abc
import asyncio
import logging
from typing import Any

log = logging.getLogger("essence.routing.protocol_router")


# ── Transport ABC ─────────────────────────────────────────────────────────────

class Transport(abc.ABC):
    """Abstract base class for all protocol transports."""

    @abc.abstractmethod
    async def send(self, uri: str, message: Any) -> Any:
        """Send a message to the given URI and return the response."""


# ── Concrete transports ───────────────────────────────────────────────────────

class RESTTransport(Transport):
    """HTTP/HTTPS REST transport via httpx."""

    async def send(self, uri: str, message: Any) -> Any:
        try:
            import httpx  # type: ignore
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(uri, json=message)
                resp.raise_for_status()
                return resp.json()
        except ImportError:
            log.warning("httpx_not_installed — REST transport unavailable")
            return {"error": "httpx not installed"}
        except Exception as exc:
            log.warning("rest_transport_error", extra={"uri": uri, "error": str(exc)[:120]})
            raise


class WebSocketTransport(Transport):
    """WebSocket transport via websockets."""

    async def send(self, uri: str, message: Any) -> Any:
        try:
            import websockets  # type: ignore
            import json
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps(message))
                raw = await ws.recv()
                return json.loads(raw)
        except ImportError:
            log.warning("websockets_not_installed — WS transport unavailable")
            return {"error": "websockets not installed"}
        except Exception as exc:
            log.warning("ws_transport_error", extra={"uri": uri, "error": str(exc)[:120]})
            raise


class A2ATransport(Transport):
    """Agent-to-Agent transport via A2AClient."""

    async def send(self, uri: str, message: Any) -> Any:
        try:
            from essence.protocols.a2a import A2AClient
            client = A2AClient(base_url=uri)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, client.send_task, message.get("message", str(message)))
            return result
        except Exception as exc:
            log.warning("a2a_transport_error", extra={"uri": uri, "error": str(exc)[:120]})
            raise


class MCPTransport(Transport):
    """MCP server transport."""

    async def send(self, uri: str, message: Any) -> Any:
        try:
            from essence.tools.mcp import MCPClient  # type: ignore
            client = MCPClient(uri)
            return await client.call(message)
        except Exception as exc:
            log.warning("mcp_transport_error", extra={"uri": uri, "error": str(exc)[:120]})
            raise


class TelegramTransport(Transport):
    """Telegram channel transport — delegates to bridge."""

    async def send(self, uri: str, message: Any) -> Any:
        try:
            from essence.channels.bridge import SystemBridge
            bridge = SystemBridge()
            text = message if isinstance(message, str) else str(message)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, bridge.send_telegram, text)
            return {"ok": True}
        except Exception as exc:
            log.warning("telegram_transport_error", extra={"error": str(exc)[:120]})
            return {"error": str(exc)}


class SlackTransport(Transport):
    """Slack channel transport — delegates to bridge."""

    async def send(self, uri: str, message: Any) -> Any:
        try:
            from essence.channels.bridge import SystemBridge
            bridge = SystemBridge()
            text = message if isinstance(message, str) else str(message)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, bridge.send_slack, text)
            return {"ok": True}
        except Exception as exc:
            log.warning("slack_transport_error", extra={"error": str(exc)[:120]})
            return {"error": str(exc)}


class DiscordTransport(Transport):
    """Discord channel transport — delegates to bridge."""

    async def send(self, uri: str, message: Any) -> Any:
        try:
            from essence.channels.bridge import SystemBridge
            bridge = SystemBridge()
            text = message if isinstance(message, str) else str(message)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, bridge.send_discord, text)
            return {"ok": True}
        except Exception as exc:
            log.warning("discord_transport_error", extra={"error": str(exc)[:120]})
            return {"error": str(exc)}


# ── Router ────────────────────────────────────────────────────────────────────

class ProtocolRouter:
    """
    Scheme-to-transport registry.

    Usage:
        router = ProtocolRouter()
        router.register("http", RESTTransport())
        await router.send("http://example.com/api", {"data": 1})
        await router.broadcast(["http://a.com", "http://b.com"], msg)
    """

    def __init__(self) -> None:
        self._registry: dict[str, Transport] = {}

    def register(self, scheme: str, transport: Transport) -> None:
        """Register a transport for a URI scheme (e.g. "http", "ws", "a2a")."""
        self._registry[scheme.lower()] = transport
        log.debug("protocol_router_registered", extra={"scheme": scheme})

    def _get_transport(self, uri: str) -> Transport:
        scheme = uri.split("://")[0].lower() if "://" in uri else "http"
        transport = self._registry.get(scheme)
        if transport is None:
            raise ValueError(
                f"No transport registered for scheme '{scheme}'. "
                f"Registered: {list(self._registry.keys())}"
            )
        return transport

    async def send(self, uri: str, message: Any) -> Any:
        """Route a message to the appropriate transport."""
        transport = self._get_transport(uri)
        return await transport.send(uri, message)

    async def broadcast(self, uris: list[str], message: Any) -> list[Any]:
        """Send the same message to multiple URIs concurrently."""
        tasks = [self.send(uri, message) for uri in uris]
        return await asyncio.gather(*tasks, return_exceptions=True)
