"""FastAPI router for /api/integrations — CRUD, health-checks, model discovery,
custom provider management, smart-router status, and PTY terminal WebSocket."""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import struct
import termios
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.server.integrations_api")

try:
    from fastapi import APIRouter, HTTPException, Body, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse
    _FA = True
except ImportError:
    _FA = False


def build_integrations_router(store: Any) -> Any:  # returns APIRouter
    if not _FA:
        raise ImportError("fastapi required")

    from fastapi import APIRouter
    from essence.integrations.registry import (
        INTEGRATION_REGISTRY, health_check_all, get_by_category,
    )
    from essence.backends.smart_router import SmartRouter

    router = APIRouter(prefix="/api/integrations", tags=["integrations"])
    _smart = SmartRouter(store)

    # ── GET /api/integrations ─────────────────────────────────────────────────
    @router.get("")
    async def list_integrations() -> dict:
        """Return all integration definitions with live config status."""
        cats: dict[str, list] = {}
        for defn in INTEGRATION_REGISTRY.values():
            configured = store.is_configured(defn.id)
            enabled    = store.is_enabled(defn.id)
            entry = {
                "id":          defn.id,
                "name":        defn.name,
                "category":    defn.category,
                "description": defn.description,
                "icon":        defn.icon,
                "docs_url":    defn.docs_url,
                "configured":  configured,
                "enabled":     enabled,
                "credential_fields": defn.credential_fields,
                "optional_fields":   defn.optional_fields,
                "settings_fields":   defn.settings_fields,
            }
            cats.setdefault(defn.category, []).append(entry)

        # Append custom providers as a synthetic entry
        custom = store.list_custom_providers()
        cats.setdefault("llm", [])
        return {"categories": cats, "custom_providers": custom}

    # ── GET /api/integrations/{id} ────────────────────────────────────────────
    @router.get("/{integration_id}")
    async def get_integration(integration_id: str) -> dict:
        defn = INTEGRATION_REGISTRY.get(integration_id)
        if defn is None and integration_id != "custom_provider":
            raise HTTPException(404, f"Unknown integration: {integration_id}")
        configured = store.is_configured(integration_id)
        creds_mask = {
            f: ("***" if store.get_credential(integration_id, f) else "")
            for f in (defn.credential_fields + defn.optional_fields if defn else [])
        }
        return {
            "id":          integration_id,
            "configured":  configured,
            "enabled":     store.is_enabled(integration_id),
            "credentials": creds_mask,
            "settings":    store.get_settings(integration_id),
        }

    # ── PUT /api/integrations/{id} ────────────────────────────────────────────
    @router.put("/{integration_id}")
    async def upsert_integration(
        integration_id: str,
        body: dict = Body(...),
    ) -> dict:
        """Save credentials and/or settings for an integration."""
        credentials = body.get("credentials", {})
        settings    = body.get("settings", {})
        enabled     = body.get("enabled", True)

        # Validate known integrations
        defn = INTEGRATION_REGISTRY.get(integration_id)
        if defn:
            required = set(defn.credential_fields)
            provided = {k for k, v in credentials.items() if v}
            if required and not required.intersection(provided):
                # Don't fail — might be partial update
                pass

        store.upsert(integration_id, credentials, settings, enabled)

        # Reload smart router so new provider is live immediately
        _smart._built = False

        # Wire side-effects
        _apply_side_effects(integration_id, store)

        return {"ok": True, "id": integration_id}

    # ── DELETE /api/integrations/{id} ─────────────────────────────────────────
    @router.delete("/{integration_id}")
    async def remove_integration(integration_id: str) -> dict:
        removed = store.remove(integration_id)
        _smart._built = False
        return {"ok": removed, "id": integration_id}

    # ── PATCH /api/integrations/{id}/enabled ──────────────────────────────────
    @router.patch("/{integration_id}/enabled")
    async def toggle_integration(
        integration_id: str,
        body: dict = Body(...),
    ) -> dict:
        enabled = bool(body.get("enabled", True))
        store.set_enabled(integration_id, enabled)
        return {"ok": True, "id": integration_id, "enabled": enabled}

    # ── POST /api/integrations/{id}/test ──────────────────────────────────────
    @router.post("/{integration_id}/test")
    async def test_integration(integration_id: str) -> dict:
        """Run the live health-check for one integration."""
        defn = INTEGRATION_REGISTRY.get(integration_id)
        if defn is None:
            if integration_id == "custom_provider":
                providers = store.list_custom_providers()
                return {"ok": len(providers) > 0,
                        "count": len(providers),
                        "providers": [p["name"] for p in providers]}
            raise HTTPException(404, f"Unknown integration: {integration_id}")
        result = await defn.health_check(store)
        return {"id": integration_id, **result}

    # ── POST /api/integrations/test/all ───────────────────────────────────────
    @router.post("/test/all")
    async def test_all_integrations() -> dict:
        results = await health_check_all(store)
        return {"results": results}

    # ── GET /api/integrations/providers/status ────────────────────────────────
    @router.get("/providers/status")
    async def provider_status() -> dict:
        """Parallel availability check + model lists for all LLM providers."""
        return await _smart.status_async()

    # ── GET /api/integrations/providers/models ────────────────────────────────
    @router.get("/providers/models")
    async def list_all_models() -> dict:
        """Discover available models from every configured provider."""
        from essence.backends.cloud import build_cloud_providers
        providers = build_cloud_providers(store)

        async def _fetch(p: Any) -> dict:
            loop = asyncio.get_running_loop()
            try:
                alive  = await loop.run_in_executor(None, p.alive)
                models = await loop.run_in_executor(None, p.list_models) \
                         if alive else []
            except Exception as exc:
                alive, models = False, []
                log.debug("model_discovery error %s: %s",
                          getattr(p, "NAME", "?"), exc)
            return {
                "provider": getattr(p, "NAME", "unknown"),
                "alive":    alive,
                "models":   models[:50],
            }

        results = await asyncio.gather(*[_fetch(p) for p in providers])
        return {"providers": list(results)}

    # ── Custom providers ───────────────────────────────────────────────────────
    @router.get("/custom/providers")
    async def list_custom() -> dict:
        return {"providers": store.list_custom_providers()}

    @router.post("/custom/providers")
    async def add_custom_provider(body: dict = Body(...)) -> dict:
        """Add or replace a custom OpenAI-compatible provider."""
        name     = body.get("name", "").strip()
        base_url = body.get("base_url", "").strip()
        if not name or not base_url:
            raise HTTPException(400, "name and base_url are required")
        provider = {
            "name":     name,
            "base_url": base_url,
            "api_key":  body.get("api_key", "sk-"),
            "models":   body.get("models", []),
            "description": body.get("description", ""),
        }
        store.upsert_custom_provider(provider)
        _smart._built = False
        return {"ok": True, "provider": name}

    @router.delete("/custom/providers/{name}")
    async def remove_custom_provider(name: str) -> dict:
        removed = store.remove_custom_provider(name)
        _smart._built = False
        return {"ok": removed, "name": name}

    @router.post("/custom/providers/{name}/test")
    async def test_custom_provider(name: str) -> dict:
        from essence.backends.cloud import CustomProvider
        providers = store.list_custom_providers()
        cp_data   = next((p for p in providers if p["name"] == name), None)
        if cp_data is None:
            raise HTTPException(404, f"Custom provider '{name}' not found")
        cp = CustomProvider(
            name=name,
            base_url=cp_data["base_url"],
            api_key=cp_data.get("api_key", "sk-"),
            models=cp_data.get("models", []),
        )
        loop   = asyncio.get_running_loop()
        alive  = await loop.run_in_executor(None, cp.alive)
        models = await loop.run_in_executor(None, cp.list_models) if alive else []
        return {"name": name, "alive": alive, "models": models[:20]}

    add_terminal_ws(router)
    return router


def _apply_side_effects(integration_id: str, store: Any) -> None:
    """Wire runtime side-effects when an integration is saved."""
    try:
        if integration_id == "sentry":
            _init_sentry(store)
        elif integration_id == "prometheus":
            _init_prometheus(store)
        elif integration_id == "telegram":
            _reload_telegram(store)
    except Exception as exc:
        log.warning("side_effect_error %s: %s", integration_id, exc)


def _init_sentry(store: Any) -> None:
    dsn = store.get_credential("sentry", "dsn")
    if not dsn:
        return
    try:
        import sentry_sdk
        env = store.get_settings("sentry").get("environment", "production")
        rate = float(store.get_settings("sentry").get("traces_sample_rate", "0.1"))
        sentry_sdk.init(dsn=dsn, environment=env, traces_sample_rate=rate)
        log.info("sentry_initialised: env=%s", env)
    except Exception as exc:
        log.warning("sentry_init_failed: %s", exc)


def _init_prometheus(store: Any) -> None:
    gw = store.get_credential("prometheus", "gateway_url")
    if not gw:
        return
    try:
        from essence.infra.metrics import push_metrics_to_gateway
        push_metrics_to_gateway(gw)
        log.info("prometheus_push_configured: %s", gw)
    except Exception as exc:
        log.debug("prometheus_init: %s", exc)


def _reload_telegram(store: Any) -> None:
    token = store.get_credential("telegram", "bot_token")
    if not token:
        return
    log.info("telegram_token_configured — restart polling to activate")


# ── PTY terminal WebSocket ──────────────────────────────────────────────────

def _set_pty_size(fd: int, cols: int, rows: int) -> None:
    """Apply TIOCSWINSZ to the PTY master fd."""
    try:
        size = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
    except Exception:
        pass


async def _pty_reader(fd: int, ws: Any, loop: asyncio.AbstractEventLoop) -> None:
    """Read PTY output and forward to WebSocket as binary."""
    while True:
        try:
            data = await loop.run_in_executor(None, os.read, fd, 4096)
            await ws.send_bytes(data)
        except (OSError, EOFError):
            break
        except Exception as exc:
            log.debug("pty_reader_error: %s", exc)
            break


async def _pty_writer(fd: int, ws: Any) -> None:
    """Receive from WebSocket and write to PTY (stdin).

    Messages can be:
    - bytes  → raw stdin from xterm.js
    - text   → JSON control message  {"type":"resize","cols":N,"rows":N}
    """
    while True:
        try:
            msg = await ws.receive()
            kind = msg.get("type")
            if kind == "websocket.disconnect":
                break
            if kind == "websocket.receive":
                text = msg.get("text")
                byt  = msg.get("bytes")
                if text:
                    try:
                        ctrl = json.loads(text)
                        if ctrl.get("type") == "resize":
                            _set_pty_size(fd, int(ctrl.get("cols", 80)),
                                          int(ctrl.get("rows", 24)))
                    except Exception:
                        os.write(fd, text.encode())
                elif byt:
                    os.write(fd, byt)
        except Exception as exc:
            log.debug("pty_writer_error: %s", exc)
            break


def add_terminal_ws(router: Any) -> None:
    """Register the /ws/terminal WebSocket endpoint on *router*."""

    @router.websocket("/ws/terminal")
    async def terminal_ws(websocket: WebSocket) -> None:  # type: ignore[misc]
        """PTY-backed interactive terminal over WebSocket."""
        await websocket.accept()
        master_fd, slave_fd = pty.openpty()
        _set_pty_size(master_fd, 220, 50)

        import subprocess
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}
        proc = subprocess.Popen(
            ["/bin/bash", "-i"],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True, env=env,
        )
        os.close(slave_fd)

        loop = asyncio.get_event_loop()
        reader_task = asyncio.create_task(_pty_reader(master_fd, websocket, loop))
        writer_task = asyncio.create_task(_pty_writer(master_fd, websocket))

        _done, pending = await asyncio.wait(
            [reader_task, writer_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            os.close(master_fd)
        except Exception:
            pass
        log.info("terminal_ws_closed")
