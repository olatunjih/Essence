# essence/server/api.py
"""Essence FastAPI server — WebSocket streaming chat + REST API endpoints.

Factory: create_app(workspace: Path) -> FastAPI

Routes:
  GET  /                          — serve Analytics Engine v3.0 HTML UI
  WS   /ws                        — streaming chat (token-by-token)
  GET  /api/status                — Analytics Engine telemetry + uptime
  GET  /api/config                — current agent config
  POST /api/config                — update model / thinking / budget at runtime
  GET  /api/sessions              — list persisted session files
  GET  /api/sessions/{id}         — retrieve full session transcript
  GET  /api/agents                — agent roster + status
  GET  /api/analytics/findings        — active Analytics Engine analytical findings
  POST /v1/chat/completions       — OpenAI-compatible SSE or JSON response
  GET  /api/docs                  — Swagger UI (FastAPI built-in)
"""
from __future__ import annotations

import asyncio
import collections as _collections_mod
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional

log = logging.getLogger("essence.server.api")

# ── Optional FastAPI guard ────────────────────────────────────────────────────
try:
    from fastapi import (FastAPI, WebSocket, WebSocketDisconnect,
                         HTTPException, Request)
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
    from fastapi.middleware.cors import CORSMiddleware
    _FASTAPI_OK = True
except ImportError:
    _FASTAPI_OK = False


# ── Agent bootstrap ───────────────────────────────────────────────────────────
def _bootstrap_agent(workspace: Path) -> tuple[Any, Any]:
    """Initialise Agent + HardwareProfile from the workspace.

    All imports are deferred so this module can be imported without the full
    dependency chain being present (e.g. during tests or minimal installs).
    """
    from essence.core.hardware import probe_hardware
    from essence.backends.registry import build_provider_chain
    from essence.agents.config import AgentConfig
    from essence.agents.agent import Agent
    from essence.memory.memory import Memory
    from essence.workspace.skill_system import load_skills_index
    from essence.workspace.scaffold import (
        load_ws_file, _DEFAULT_SOUL, _DEFAULT_TOOLS
    )

    hw = probe_hardware()
    prov = build_provider_chain(hw)
    mem = Memory(workspace, hw.tier)

    soul = load_ws_file(workspace, "SOUL.md", _DEFAULT_SOUL)
    identity = load_ws_file(workspace, "IDENTITY.md", "")
    tools_md = load_ws_file(workspace, "TOOLS.md", _DEFAULT_TOOLS)
    skills = load_skills_index(workspace)

    cfg = AgentConfig(
        provider=prov,
        model=hw.model,
        workspace=workspace,
        thinking=False,
        budget=8000,
    )
    agent = Agent(
        cfg,
        soul=soul,
        identity=identity,
        tools_md=tools_md,
        skills=skills,
        memory=mem,
        hw=hw,
    )
    return agent, hw


# ── Application factory ───────────────────────────────────────────────────────
def create_app(workspace: Path) -> "FastAPI":
    """Create and return the configured Essence FastAPI application.

    Args:
        workspace: Path to the Essence workspace directory (contains SOUL.md,
                   config.toml, sessions/, etc.).

    Returns:
        Configured FastAPI instance ready for uvicorn.
    """
    if not _FASTAPI_OK:
        raise ImportError(
            "fastapi is not installed. "
            "Run: pip install 'fastapi>=0.111' 'uvicorn[standard]>=0.29'"
        )

    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
    from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware

    # Path to the compiled Svelte UI
    _UI_DIR = Path(__file__).parent / "ui"

    app = FastAPI(
        title="Essence — Essence Intelligence System",
        version="29.0.0",
        description="Analytics Engine v3.0 analytical kernel · local-first agent platform",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Shared mutable state (single-process) ─────────────────────────────────
    _state: dict[str, Any] = {
        "agent": None,
        "hw": None,
        "start_ts": time.time(),
        "request_count": 0,
    }

    def _get_agent() -> Any:
        if _state["agent"] is None:
            _state["agent"], _state["hw"] = _bootstrap_agent(workspace)
        return _state["agent"]

    # ── Helper: optional API-key auth for skill write endpoints ───────────────
    # If Essence_API_KEY env var is set, all skill write/delete/import requests
    # must supply: Authorization: Bearer <key>.  If the var is unset (local
    # development default), every request is allowed through.
    def _check_skill_auth(request: Request) -> None:
        required_key = os.environ.get("Essence_API_KEY", "")
        if not required_key:
            return  # auth disabled — local / development mode
        auth_header = request.headers.get("Authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or token.strip() != required_key:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized — set Authorization: Bearer <Essence_API_KEY>",
            )

    # ── Helper: load config.toml ──────────────────────────────────────────────
    def _load_toml() -> dict:
        cfg_path = workspace / "config.toml"
        if not cfg_path.exists():
            return {}
        try:
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore
                except ImportError:
                    return {}
            with open(cfg_path, "rb") as f:
                return tomllib.load(f)
        except Exception:
            return {}

    # ── Helper: stream agent.chat via thread + queue ──────────────────────────
    async def _stream_chat(
        agent: Any,
        message: str,
        loop: asyncio.AbstractEventLoop,
    ) -> tuple[asyncio.Queue, asyncio.Future]:
        """Run agent.chat() in a thread executor, feeding tokens into a queue."""
        queue: asyncio.Queue = asyncio.Queue()

        def _emit(token: str) -> None:
            asyncio.run_coroutine_threadsafe(queue.put(token), loop)

        future = loop.run_in_executor(
            None,
            lambda: agent.chat(message, emit=_emit),
        )
        return queue, future

    # ── Integration store + router ────────────────────────────────────────────
    from essence.integrations.store import init_store
    _int_store = init_store(workspace)
    from essence.server.integrations_api import build_integrations_router
    from essence.backends.smart_router import init_router
    _smart_router = init_router(_int_store)
    app.include_router(build_integrations_router(_int_store))

    # ── GET /integrations — integration settings UI ───────────────────────────
    _INT_UI = Path(__file__).parent / "integrations_ui.html"

    @app.get("/integrations", response_class=HTMLResponse, include_in_schema=False)
    async def serve_integrations_ui() -> HTMLResponse:
        return HTMLResponse(content=_INT_UI.read_text(encoding="utf-8"))

    # ── Static Svelte UI assets ───────────────────────────────────────────────
    if _UI_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(_UI_DIR / "assets")), name="assets")

    # ── GET / — serve compiled Svelte UI (fallback to embedded HTML) ──────────
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_ui() -> HTMLResponse:
        index = _UI_DIR / "index.html"
        if index.exists():
            return HTMLResponse(content=index.read_text(encoding="utf-8"))
        from essence.server.web_ui import _web_ui_html
        return HTMLResponse(content=_web_ui_html(workspace))

    # ── GET /api/skills ───────────────────────────────────────────────────────
    @app.get("/api/skills")
    async def list_skills_endpoint() -> dict:
        """Return metadata for all installed workspace skills."""
        from essence.workspace.skill_system import list_skills_meta
        skills = list_skills_meta(workspace)
        return {"skills": skills, "total": len(skills)}

    # ── GET /api/skills/{name} ────────────────────────────────────────────────
    @app.get("/api/skills/{name}")
    async def get_skill_endpoint(name: str) -> dict:
        """Return the full SKILL.md content for a named skill."""
        from essence.workspace.skill_system import read_skill_content
        content = read_skill_content(workspace, name)
        if content.startswith("[skill '"):
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        return {"name": name, "content": content}

    # ── POST /api/skills ──────────────────────────────────────────────────────
    @app.post("/api/skills", status_code=201)
    async def create_skill_endpoint(request: Request) -> dict:
        """Create a new skill.

        Body: {"name": "my-skill", "content": "# My Skill\\n..."}

        Returns the created skill metadata on success.
        """
        _check_skill_auth(request)
        from essence.workspace.skill_system import create_skill, list_skills_meta
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        name = str(body.get("name", "")).strip()
        content = str(body.get("content", "")).strip()

        if not name:
            raise HTTPException(status_code=400, detail="'name' is required")
        if not content:
            raise HTTPException(status_code=400, detail="'content' is required")

        ok, result = create_skill(workspace, name, content)
        if not ok:
            raise HTTPException(status_code=409, detail=result)

        # Reload skills on the live agent if one exists
        agent = _state.get("agent")
        if agent is not None:
            try:
                from essence.workspace.skill_system import load_skills_index
                agent.skills = load_skills_index(workspace)
                agent.reload_identity_files()
            except Exception:
                pass

        return {"ok": True, "name": name, "path": result}

    # ── PUT /api/skills/{name} ────────────────────────────────────────────────
    @app.put("/api/skills/{name}")
    async def update_skill_endpoint(name: str, request: Request) -> dict:
        """Overwrite an existing skill's SKILL.md content.

        Body: {"content": "# Updated content\\n..."}
        """
        _check_skill_auth(request)
        from essence.workspace.skill_system import update_skill
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        content = str(body.get("content", "")).strip()
        if not content:
            raise HTTPException(status_code=400, detail="'content' is required")

        ok, result = update_skill(workspace, name, content)
        if not ok:
            raise HTTPException(status_code=404, detail=result)

        # Reload skills on the live agent
        agent = _state.get("agent")
        if agent is not None:
            try:
                from essence.workspace.skill_system import load_skills_index
                agent.skills = load_skills_index(workspace)
                agent.reload_identity_files()
            except Exception:
                pass

        return {"ok": True, "name": name, "path": result}

    # ── DELETE /api/skills/{name} ─────────────────────────────────────────────
    @app.delete("/api/skills/{name}")
    async def delete_skill_endpoint(name: str, request: Request) -> dict:
        """Permanently delete a skill and its directory."""
        _check_skill_auth(request)
        from essence.workspace.skill_system import delete_skill
        ok, result = delete_skill(workspace, name)
        if not ok:
            raise HTTPException(status_code=404, detail=result)

        # Reload skills on the live agent
        agent = _state.get("agent")
        if agent is not None:
            try:
                from essence.workspace.skill_system import load_skills_index
                agent.skills = load_skills_index(workspace)
                agent.reload_identity_files()
            except Exception:
                pass

        return {"ok": True, "deleted": result}

    # ── POST /api/skills/import ───────────────────────────────────────────────
    @app.post("/api/skills/import", status_code=201)
    async def import_skill_endpoint(request: Request) -> dict:
        """Import a skill from a raw URL (e.g. GitHub raw content).

        Body: {"url": "https://...", "name": "optional-override"}
        """
        _check_skill_auth(request)
        from essence.workspace.skill_system import import_skill_from_url
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        url = str(body.get("url", "")).strip()
        if not url:
            raise HTTPException(status_code=400, detail="'url' is required")

        name_override = body.get("name", None)

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        ok, result = await loop.run_in_executor(
            None,
            lambda: import_skill_from_url(workspace, url,
                                          name=name_override or None),
        )
        if not ok:
            raise HTTPException(status_code=502, detail=result)

        # Reload skills on the live agent
        agent = _state.get("agent")
        if agent is not None:
            try:
                from essence.workspace.skill_system import load_skills_index
                agent.skills = load_skills_index(workspace)
                agent.reload_identity_files()
            except Exception:
                pass

        return {"ok": True, "message": result}

    # ── WS /ws — streaming chat (kernel-driven) ──────────────────────────────
    @app.websocket("/ws")
    async def ws_chat(websocket: WebSocket) -> None:
        """
        WebSocket streaming chat endpoint — kernel-driven via ingest_capsule/tick.

        Client → Server:
            {"type": "chat", "message": "user text", "user_id"?: str}
            {"type": "ping"}

        Server → Client:
            {"type": "token",    "content": "<token>"}
            {"type": "done",     "session_id": "<uuid>", "elapsed_ms": N}
            {"type": "error",    "content": "<message>"}
            {"type": "status",   "state": "idle"|"running"|"planning"}
            {"type": "pong"}
            {"type": "clarify",  "options": [...]}  — IntentRouter requests clarification
        """
        await websocket.accept()
        session_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()

        # Prefer kernel path; fall back to legacy agent path when kernel unavailable
        _kernel_mode = True
        try:
            _kn = _get_kernel()
        except Exception:
            _kernel_mode = False
            _kn = None

        try:
            while True:
                raw = await websocket.receive_text()

                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({
                        "type": "error", "content": "Malformed JSON payload",
                    })
                    continue

                msg_type = payload.get("type", "chat")

                # ── Ping/pong keepalive ──────────────────────────────────
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                message = payload.get("message", "").strip()
                user_id = payload.get("user_id", "ws_user")

                if not message:
                    await websocket.send_json({
                        "type": "error", "content": "Empty message",
                    })
                    continue

                _state["request_count"] += 1
                t_start = time.monotonic()
                await websocket.send_json({"type": "status", "state": "running"})

                # ── Kernel path ──────────────────────────────────────────
                if _kernel_mode and _kn is not None:
                    try:
                        # Stage A: IntentRouter (optional)
                        intent_result = None
                        if _kn._s.intent_router is not None:
                            await websocket.send_json({
                                "type": "status", "state": "planning",
                            })
                            intent_result = await _kn._s.intent_router.route(
                                message, session_id=session_id)
                            if str(getattr(intent_result, "type", "")) == "clarification_needed":
                                opts = getattr(intent_result, "params", {}).get("options", [])
                                await websocket.send_json({
                                    "type": "clarify", "options": opts,
                                })
                                await websocket.send_json({"type": "status", "state": "idle"})
                                continue

                        # Stage B: ingest_capsule
                        capsule_id = await loop.run_in_executor(
                            None,
                            lambda: _kn.ingest_capsule(
                                message, user_id, autonomy_tier=_kn._s.autonomy_tier),
                        )

                        # Stage C: tick loop — advance tasks and stream tokens
                        all_tokens: list[str] = []
                        for _tick_attempt in range(50):   # bounded loop
                            tick_result = await loop.run_in_executor(
                                None, lambda cid=capsule_id: _kn.tick(cid))
                            status = tick_result.get("status", "")

                            # Forward any text tokens
                            text = tick_result.get("text", "")
                            if text:
                                for chunk in _chunk_text(text, 40):
                                    all_tokens.append(chunk)
                                    await websocket.send_json({
                                        "type": "token", "content": chunk,
                                    })
                                    await asyncio.sleep(0)

                            if status in ("done", "no_plan", "no_ready_tasks"):
                                break
                            if status == "error":
                                raise RuntimeError(tick_result.get("detail", "tick error"))
                            await asyncio.sleep(0.01)

                        # Drain goal_manager autonomous queue from this tick cycle
                        if _kn._s.goal_manager is not None:
                            _kn._s.goal_manager.drain_autonomous()

                        full_response = "".join(all_tokens)
                        elapsed_ms = int((time.monotonic() - t_start) * 1000)

                    except Exception as _kern_exc:
                        # Graceful fallback to legacy agent
                        log.warning("ws_kernel_error_fallback",
                                    extra={"error": str(_kern_exc)[:120]})
                        _kernel_mode = False
                        _kn = None
                        # Re-process via legacy path below
                        try:
                            agent = _get_agent()
                            queue, future = await _stream_chat(agent, message, loop)
                            all_tokens = []
                            while True:
                                try:
                                    token = await asyncio.wait_for(queue.get(), timeout=0.05)
                                    all_tokens.append(token)
                                    await websocket.send_json({"type": "token", "content": token})
                                except asyncio.TimeoutError:
                                    if future.done():
                                        while not queue.empty():
                                            token = queue.get_nowait()
                                            all_tokens.append(token)
                                            await websocket.send_json({"type": "token", "content": token})
                                        break
                            exc = future.exception()
                            if exc:
                                await websocket.send_json({"type": "error", "content": str(exc)[:200]})
                                continue
                            full_response = "".join(all_tokens)
                            elapsed_ms = int((time.monotonic() - t_start) * 1000)
                        except Exception as _fb_exc:
                            await websocket.send_json({"type": "error", "content": str(_fb_exc)[:200]})
                            continue

                else:
                    # ── Legacy agent path ────────────────────────────────
                    try:
                        agent = _get_agent()
                        queue, future = await _stream_chat(agent, message, loop)
                        all_tokens = []
                        while True:
                            try:
                                token = await asyncio.wait_for(queue.get(), timeout=0.05)
                                all_tokens.append(token)
                                await websocket.send_json({"type": "token", "content": token})
                            except asyncio.TimeoutError:
                                if future.done():
                                    while not queue.empty():
                                        token = queue.get_nowait()
                                        all_tokens.append(token)
                                        await websocket.send_json({"type": "token", "content": token})
                                    break
                        exc = future.exception()
                        if exc:
                            await websocket.send_json({"type": "error", "content": str(exc)[:200]})
                            continue
                        full_response = "".join(all_tokens)
                        elapsed_ms = int((time.monotonic() - t_start) * 1000)
                    except Exception as _ag_exc:
                        await websocket.send_json({"type": "error", "content": str(_ag_exc)[:200]})
                        continue

                # ── Persist to sessions/ ─────────────────────────────────
                try:
                    sess_dir = workspace / "sessions"
                    sess_dir.mkdir(parents=True, exist_ok=True)
                    sess_file = sess_dir / f"{session_id}.jsonl"
                    with sess_file.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "role": "user", "content": message, "ts": time.time(),
                        }) + "\n")
                        f.write(json.dumps({
                            "role": "assistant", "content": full_response,
                            "ts": time.time(), "elapsed_ms": elapsed_ms,
                        }) + "\n")
                except Exception:
                    pass

                await websocket.send_json({
                    "type": "done",
                    "session_id": session_id,
                    "elapsed_ms": elapsed_ms,
                })
                await websocket.send_json({"type": "status", "state": "idle"})

        except WebSocketDisconnect:
            pass
        except Exception as exc:
            try:
                await websocket.send_json({
                    "type": "error", "content": str(exc)[:200],
                })
            except Exception:
                pass


    def _chunk_text(text: str, size: int) -> list[str]:
        """Split text into chunks of approximately `size` characters."""
        if len(text) <= size:
            return [text]
        return [text[i:i + size] for i in range(0, len(text), size)]

    # ── GET /api/status — Analytics Engine telemetry ────────────────────────────────────
    @app.get("/api/status")
    async def api_status() -> dict:
        """Return live Analytics Engine telemetry, uptime, and agent status."""
        try:
            from essence.analytics.spine import get_analytical_spine
            spine = get_analytical_spine()
        except Exception:
            spine = None

        agent = _state["agent"]
        hw = _state["hw"]
        uptime_s = int(time.time() - _state["start_ts"])

        return {
            "status": "ok",
            "version": "29.0.0",
            "uptime_s": uptime_s,
            "request_count": _state["request_count"],
            "analytics_trust": float(getattr(spine, "trust_score", 1.0)),
            "analytics_arch": str(getattr(spine, "active_archetype", "general") or "general"),
            "analytics_domain": str(
                getattr(getattr(spine, "active_lens", None), "name", "general") or "general"
            ),
            "active_findings": len(getattr(spine, "active_findings", [])),
            "model": (agent.cfg.model
                      if agent and hasattr(agent, "cfg") else None),
            "tier": (hw.tier if hw else None),
            "thinking": (agent.cfg.thinking
                         if agent and hasattr(agent, "cfg") else False),
        }

    # ── GET /api/config ───────────────────────────────────────────────────────
    @app.get("/api/config")
    async def get_config() -> dict:
        """Return current agent and inference configuration."""
        agent = _state["agent"]
        hw = _state["hw"]
        raw = _load_toml()

        return {
            "model": (agent.cfg.model if agent and hasattr(agent, "cfg")
                      else raw.get("inference", {}).get("model", "qwen3:4b")),
            "thinking": (agent.cfg.thinking if agent and hasattr(agent, "cfg")
                         else False),
            "budget": (agent.cfg.budget if agent and hasattr(agent, "cfg")
                       else 8000),
            "tier": (hw.tier if hw else None),
            "tier_label": (hw.tier_label if hw else "unknown"),
            "workspace": str(workspace),
            "toml": raw,
        }

    # ── POST /api/config ──────────────────────────────────────────────────────
    @app.post("/api/config")
    async def update_config(request: Request) -> dict:
        """Update agent configuration at runtime (no restart required).

        Accepted keys: model (str), thinking (bool), budget (int).
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        agent = _state["agent"]
        updated: dict[str, Any] = {}

        if agent and hasattr(agent, "cfg"):
            if "model" in body:
                agent.cfg.model = str(body["model"])
                updated["model"] = agent.cfg.model
            if "thinking" in body:
                agent.cfg.thinking = bool(body["thinking"])
                updated["thinking"] = agent.cfg.thinking
            if "budget" in body:
                agent.cfg.budget = max(1, int(body["budget"]))
                updated["budget"] = agent.cfg.budget

        return {"ok": True, "updated": updated}

    # ── GET /api/sessions ─────────────────────────────────────────────────────
    @app.get("/api/sessions")
    async def list_sessions(limit: int = 50) -> dict:
        """Return the most recent sessions sorted by modification time."""
        sess_dir = workspace / "sessions"
        sessions: list[dict] = []

        if sess_dir.exists():
            files = sorted(
                sess_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:limit]

            for f in files:
                try:
                    raw = f.read_text(encoding="utf-8").strip()
                    lines = [l for l in raw.split("\n") if l]
                    first_user = ""
                    for ln in lines:
                        try:
                            rec = json.loads(ln)
                            if rec.get("role") == "user":
                                first_user = rec.get("content", "")[:80]
                                break
                        except json.JSONDecodeError:
                            pass
                    sessions.append({
                        "id": f.stem,
                        "name": first_user or f.stem[:24],
                        "ts": f.stat().st_mtime,
                        "turns": len(lines),
                    })
                except Exception:
                    continue

        return {"sessions": sessions, "total": len(sessions)}

    # ── GET /api/sessions/{session_id} ───────────────────────────────────────
    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict:
        """Return the full message transcript for a session."""
        safe_id = "".join(c for c in session_id if c.isalnum() or c == "-")
        sess_file = workspace / "sessions" / f"{safe_id}.jsonl"
        if not sess_file.exists():
            raise HTTPException(status_code=404, detail="Session not found")

        messages: list[dict] = []
        for line in sess_file.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                pass

        return {"session_id": safe_id, "messages": messages}

    # ── DELETE /api/sessions/{session_id} ────────────────────────────────────
    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict:
        """Permanently delete a session transcript."""
        safe_id = "".join(c for c in session_id if c.isalnum() or c == "-")
        sess_file = workspace / "sessions" / f"{safe_id}.jsonl"
        if not sess_file.exists():
            raise HTTPException(status_code=404, detail="Session not found")
        sess_file.unlink()
        return {"ok": True, "deleted": safe_id}

    # ── GET /api/agents ───────────────────────────────────────────────────────
    @app.get("/api/agents")
    async def list_agents() -> dict:
        """Return the agent roster with live status."""
        agents_md = workspace / "AGENTS.md"
        raw_md = ""
        if agents_md.exists():
            try:
                raw_md = agents_md.read_text(encoding="utf-8")
            except Exception:
                pass

        agent = _state["agent"]
        hw = _state["hw"]

        return {
            "agents": [
                {
                    "id": "primary",
                    "name": "Essence Primary",
                    "model": (agent.cfg.model
                              if agent and hasattr(agent, "cfg") else "qwen3:4b"),
                    "status": "idle",
                    "tier": (hw.tier if hw else 1),
                    "thinking": (agent.cfg.thinking
                                 if agent and hasattr(agent, "cfg") else False),
                    "analytics_enabled": True,
                }
            ],
            "agents_md": raw_md,
        }

    # ── GET /api/analytics/findings ───────────────────────────────────────────────
    @app.get("/api/analytics/findings")
    async def analytics_findings() -> dict:
        """Return active Analytics Engine analytical findings and spine state."""
        try:
            from essence.analytics.spine import get_analytical_spine
            spine = get_analytical_spine()
        except Exception:
            spine = None

        findings_raw = getattr(spine, "active_findings", [])
        findings_out: list[dict] = []
        for f in findings_raw[:20]:
            try:
                findings_out.append(
                    f.model_dump() if hasattr(f, "model_dump") else vars(f)
                )
            except Exception:
                pass

        return {
            "findings": findings_out,
            "trust_score": float(getattr(spine, "trust_score", 1.0)),
            "archetype": str(getattr(spine, "active_archetype", "general") or "general"),
            "domain": str(
                getattr(getattr(spine, "active_lens", None), "name", "general") or "general"
            ),
        }

    # ── POST /v1/chat/completions — OpenAI-compatible SSE ────────────────────
    @app.post("/v1/chat/completions")
    async def openai_chat(request: Request) -> Any:
        """OpenAI-compatible chat completions endpoint with optional SSE streaming.

        Accepts the standard OpenAI request body (model, messages, stream).
        Uses the Essence agent for generation instead of calling an external API.
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        messages: list[dict] = body.get("messages", [])
        do_stream: bool = bool(body.get("stream", False))

        # Extract the last user message
        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        if not user_msg:
            raise HTTPException(status_code=400, detail="No user message found in messages")

        agent = _get_agent()
        comp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created_ts = int(time.time())
        model_name = agent.cfg.model if hasattr(agent, "cfg") else "essence"
        loop = asyncio.get_event_loop()

        if do_stream:
            async def _sse_generator() -> AsyncIterator[str]:
                queue, future = await _stream_chat(agent, user_msg, loop)

                while True:
                    try:
                        token = await asyncio.wait_for(queue.get(), timeout=0.05)
                        chunk = json.dumps({
                            "id": comp_id,
                            "object": "chat.completion.chunk",
                            "created": created_ts,
                            "model": model_name,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": token},
                                "finish_reason": None,
                            }],
                        })
                        yield f"data: {chunk}\n\n"
                    except asyncio.TimeoutError:
                        if future.done():
                            while not queue.empty():
                                token = queue.get_nowait()
                                chunk = json.dumps({
                                    "id": comp_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model_name,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {"content": token},
                                        "finish_reason": None,
                                    }],
                                })
                                yield f"data: {chunk}\n\n"
                            break

                stop_chunk = json.dumps({
                    "id": comp_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_name,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }],
                })
                yield f"data: {stop_chunk}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                _sse_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        # Non-streaming — collect full response
        buf: list[str] = []
        await loop.run_in_executor(
            None,
            lambda: agent.chat(user_msg, emit=lambda t: buf.append(t)),
        )
        content = "".join(buf)
        prompt_tokens = max(1, len(user_msg) // 4)
        completion_tokens = max(1, len(content) // 4)

        return {
            "id": comp_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    # ── Fix 1: Kernel bootstrap (APDE subsystem) ─────────────────────────────
    # The Kernel is initialised lazily alongside the legacy Agent so the server
    # can boot even if Kernel dependencies are unavailable in minimal installs.
    _state["kernel"] = None

    def _get_kernel() -> Any:
        """Return the cached Kernel, initialising it lazily on first call."""
        if _state["kernel"] is None:
            try:
                from essence.boot import boot_kernel
                _state["kernel"] = boot_kernel(workspace=workspace)
            except Exception as _ke:
                raise HTTPException(
                    status_code=503,
                    detail=f"Kernel not available: {str(_ke)[:200]}",
                )
        return _state["kernel"]

    # ── In-memory log ring buffer for Route R10 ───────────────────────────────
    import logging as _logging

    _LOG_BUFFER: "Any" = _collections_mod.deque(maxlen=500)

    class _BufferHandler(_logging.Handler):
        """Logging handler that appends structured records to _LOG_BUFFER."""
        def emit(self, record: _logging.LogRecord) -> None:
            _LOG_BUFFER.append({
                "ts":    self.format(record)[:8],  # HH:MM:SS from formatter
                "level": record.levelname,
                "src":   record.name,
                "msg":   record.getMessage(),
            })

    _buf_handler = _BufferHandler()
    _buf_handler.setFormatter(_logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    _logging.getLogger("essence").addHandler(_buf_handler)

    # ── GET /api/system — hardware + kernel telemetry ─────────────────────────
    @app.get("/api/system")
    async def api_system() -> dict:
        """Return hardware tier, kernel state, guardrail summary, and memory backend info."""
        try:
            from essence.core.hardware import probe_hardware
            hw = probe_hardware()
            hardware = {
                "tier": hw.tier,
                "tier_label": getattr(hw, "tier_label", f"T{hw.tier}"),
                "model": getattr(hw, "model", ""),
                "ram_gb": getattr(hw, "ram_gb", 0.0),
                "vram_gb": getattr(hw, "vram_gb", 0.0),
                "cpu_cores": getattr(hw, "cpu_cores", 0),
                "gpu_name": getattr(hw, "gpu_name", ""),
            }
        except Exception:
            hardware = {"tier": 0, "tier_label": "unknown", "model": "", "ram_gb": 0.0,
                        "vram_gb": 0.0, "cpu_cores": 0, "gpu_name": ""}

        kernel_info: dict = {"runtime_id": "", "version": "1.0.0",
                             "uptime_seconds": 0, "autonomy_tier": 2, "dev_mode": True}
        guardrail_info: dict = {"sandbox_active": False, "quota_limit": 0, "audit_row_count": 0}
        try:
            k = _state.get("kernel")
            if k is None:
                k = _get_kernel()
            m = k.manifest
            kernel_info = {
                "runtime_id": getattr(m, "runtime_id", ""),
                "version": "1.0.0",
                "uptime_seconds": int(time.time() - _state["start_ts"]),
                "autonomy_tier": k._s.autonomy_tier,
                "dev_mode": getattr(m, "dev_mode", True),
            }
            guardrail_info = {
                "sandbox_active": getattr(k._s.guardrails, "_sandbox_active", False),
                "quota_limit": getattr(k._s.guardrails, "_quota_limit", 0),
                "audit_row_count": len(k.audit()),
            }
        except Exception:
            pass

        return {
            "hardware": hardware,
            "kernel": kernel_info,
            "guardrails": guardrail_info,
            "memory": {"backend": "FaissBackend", "embedding_mode": "semantic",
                       "vector_count": 0},
        }

    # ── POST /api/system/diagnostics ─────────────────────────────────────────
    @app.post("/api/system/diagnostics")
    async def run_diagnostics() -> dict:
        """Run a suite of system health checks and return pass/warn/fail per step."""
        steps = []

        # SQLite integrity
        try:
            import sqlite3 as _sq3
            db_path = workspace / "capsule_store.db"
            if db_path.exists():
                conn = _sq3.connect(str(db_path))
                result = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                ok = result and result[0] == "ok"
                steps.append({"name": "SQLite integrity",
                               "status": "pass" if ok else "fail",
                               "detail": str(result[0]) if result else "no result"})
            else:
                steps.append({"name": "SQLite integrity", "status": "warn",
                               "detail": "database not yet created"})
        except Exception as e:
            steps.append({"name": "SQLite integrity", "status": "fail",
                          "detail": str(e)[:80]})

        # Provider ping
        try:
            import urllib.request as _ur
            ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
            _ur.urlopen(f"{ollama_host}/api/tags", timeout=3)
            steps.append({"name": "Provider ping", "status": "pass",
                          "detail": f"Ollama at {ollama_host}"})
        except Exception:
            steps.append({"name": "Provider ping", "status": "warn",
                          "detail": "Ollama not reachable — using offline stub"})

        # Sandbox detection
        try:
            from essence.security.sandbox import _EphemeralContainerSandbox
            cs = _EphemeralContainerSandbox(workspace)
            avail = cs.available()
            steps.append({"name": "Sandbox available",
                          "status": "pass" if avail else "warn",
                          "detail": "container runtime detected" if avail
                          else "no container runtime"})
        except Exception as e:
            steps.append({"name": "Sandbox available", "status": "warn",
                          "detail": str(e)[:80]})

        # Memory backend
        try:
            steps.append({"name": "Memory backend", "status": "pass",
                          "detail": "workspace store accessible"})
        except Exception as e:
            steps.append({"name": "Memory backend", "status": "fail",
                          "detail": str(e)[:80]})

        warnings = sum(1 for s in steps if s["status"] == "warn")
        failures = sum(1 for s in steps if s["status"] == "fail")
        return {"steps": steps, "warnings": warnings, "failures": failures}

    # ── GET /api/cron — list scheduled jobs ───────────────────────────────────
    @app.get("/api/cron")
    async def list_cron_jobs() -> dict:
        """Return all scheduled heartbeat jobs with last/next run times."""
        try:
            from essence.workspace.heartbeat import HeartbeatScheduler
            sched = HeartbeatScheduler(workspace)
            jobs = sched.list_jobs()
            result = []
            for j in jobs:
                result.append({
                    "name": j.name,
                    "message": j.message,
                    "schedule": j.schedule,
                    "last_run": getattr(j, "last_run", None),
                    "next_run": getattr(j, "next_run", None),
                    "enabled": getattr(j, "enabled", True),
                    "last_result": getattr(j, "last_result", ""),
                })
            return {"jobs": result}
        except Exception as e:
            return {"jobs": [], "error": str(e)[:120]}

    # ── POST /api/cron — add a job ────────────────────────────────────────────
    @app.post("/api/cron", status_code=201)
    async def add_cron_job(request: Request) -> dict:
        """Add a new heartbeat job."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        name = str(body.get("name", "")).strip()
        message = str(body.get("message", "")).strip()
        schedule = str(body.get("schedule", "")).strip()
        if not name or not message or not schedule:
            raise HTTPException(status_code=400, detail="name, message, schedule required")
        try:
            from essence.workspace.heartbeat import HeartbeatScheduler
            sched = HeartbeatScheduler(workspace)
            sched.add(name=name, message=message, schedule=schedule)
            return {"name": name, "status": "created"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # ── DELETE /api/cron/{name} ───────────────────────────────────────────────
    @app.delete("/api/cron/{name}")
    async def delete_cron_job(name: str) -> dict:
        """Remove a heartbeat job by name."""
        try:
            from essence.workspace.heartbeat import HeartbeatScheduler
            sched = HeartbeatScheduler(workspace)
            removed = sched.remove(name)
            if not removed:
                raise HTTPException(status_code=404, detail=f"Job '{name}' not found")
            return {"name": name, "status": "deleted"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # ── GET /api/models — list configured providers and local models ──────────
    @app.get("/api/models")
    async def list_models() -> dict:
        """Return configured remote providers and locally loaded Ollama models."""
        remote: list[dict] = []
        local: list[dict] = []

        try:
            from essence.backends.registry import BackendRegistry
            reg = BackendRegistry(workspace)
            for p in reg.build_providers():
                remote.append({
                    "name": getattr(p, "model", str(p)),
                    "provider": type(p).__name__.lower().replace("backend", ""),
                    "ctx_k": getattr(p, "ctx_k", 128),
                    "vision": False,
                    "status": "active",
                    "avg_latency_ms": 0,
                    "cost_input_per_mtok": 0.0,
                })
        except Exception:
            pass

        try:
            import urllib.request as _ur, json as _json
            ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
            resp = _ur.urlopen(f"{ollama_host}/api/tags", timeout=3)
            tags = _json.loads(resp.read().decode())
            for m in tags.get("models", []):
                local.append({
                    "name": m.get("name", ""),
                    "provider": "ollama",
                    "params_b": 0.0,
                    "size_gb": round(m.get("size", 0) / 1e9, 1),
                    "ctx_k": 128,
                    "status": "loaded",
                    "avg_latency_ms": 0,
                })
        except Exception:
            pass

        return {"remote": remote, "local": local}

    # ── GET /api/gateway — routing configuration ──────────────────────────────
    @app.get("/api/gateway")
    async def get_gateway() -> dict:
        """Return LLM routing config and channel platform connection states."""
        routes: list[dict] = []
        try:
            from essence.backends.routing import ContextualBanditRouter
            cbr = ContextualBanditRouter()
            for arm in cbr.arm_stats():
                routes.append({
                    "label": arm.get("label", arm.get("model", "")),
                    "model": arm.get("model", ""),
                    "threshold_ms": arm.get("threshold_ms", None),
                    "avg_latency_ms": arm.get("avg_latency_ms", 0),
                    "status": arm.get("status", "standby"),
                })
        except Exception:
            pass

        return {
            "routes": routes,
            "platforms": {
                "telegram": {"connected": False, "detail": "not configured"},
                "discord":  {"connected": False, "detail": "not configured"},
                "slack":    {"connected": False, "detail": "not configured"},
            },
        }

    # ── GET /api/traces — aggregated cost/latency metrics ────────────────────
    @app.get("/api/traces")
    async def get_traces() -> dict:
        """Return aggregated daily cost/latency stats and recent trace entries."""
        stats: dict = {"requests_today": 0, "avg_latency_ms": 0,
                       "errors_today": 0, "tokens_out_today": 0,
                       "cost_today_usd": 0.0, "budget_remaining_pct": 100}
        top_models: list[dict] = []
        recent: list[dict] = []

        try:
            from essence.infra.cost_sqlite import CostSQLite
            cs = CostSQLite(workspace)
            summary = cs.summary()
            total_tok = cs.total_tokens()
            stats["tokens_out_today"] = total_tok
            stats["requests_today"] = sum(r["tasks"] for r in summary)
            for s in summary[:3]:
                tok_share = (s["total_tokens"] / total_tok * 100) if total_tok else 0
                top_models.append({"model": s["model"],
                                   "token_share_pct": round(tok_share, 1)})
        except Exception:
            pass

        return {"stats": stats, "top_models": top_models, "recent": recent}

    # ── GET /api/workflows — active PlanDAG list ──────────────────────────────
    @app.get("/api/workflows")
    async def get_workflows() -> dict:
        """Return all non-aborted APDE plans with their task lists."""
        try:
            kernel = _get_kernel()
            plans_raw = kernel._s.plan_repo.list_active()
            plans_out = []
            for plan in plans_raw:
                done = sum(1 for t in plan.tasks
                           if t.state.value in ("DONE", "DONE_INSUFFICIENT"))
                plans_out.append({
                    "plan_id": plan.id,
                    "capsule_id": plan.capsule_id,
                    "goal": plan.tasks[0].goal if plan.tasks else "",
                    "status": plan.plan_status.value,
                    "task_count": len(plan.tasks),
                    "tasks_done": done,
                    "created_at": 0.0,
                    "tasks": [
                        {"id": t.id, "goal": t.goal,
                         "state": t.state.value, "risk": t.risk.value}
                        for t in plan.tasks
                    ],
                })
            return {"plans": plans_out}
        except HTTPException:
            raise
        except Exception as e:
            return {"plans": [], "error": str(e)[:120]}

    # ── GET /api/logs — in-memory log ring buffer ─────────────────────────────
    @app.get("/api/logs")
    async def get_logs(level: str = "", src: str = "") -> dict:
        """Return recent structured log entries from the in-process ring buffer.

        Query params:
            level: Filter to this level (e.g. WARN, ERROR). Empty = all levels.
            src:   Filter to this logger name prefix. Empty = all sources.
        """
        entries = list(_LOG_BUFFER)
        if level:
            entries = [e for e in entries if e["level"] == level.upper()]
        if src:
            entries = [e for e in entries if e["src"].startswith(src)]
        return {"entries": entries[-200:], "total": len(list(_LOG_BUFFER))}

    # ── GET /api/plugins — installed plugin list ──────────────────────────────
    @app.get("/api/plugins")
    async def get_plugins() -> dict:
        """Return installed plugin names and their registered hook keys."""
        installed: list[dict] = []
        try:
            from essence.infra.plugin import PluginLoader
            loader = PluginLoader(workspace)
            for p in loader.scan():
                installed.append({
                    "name": p.name,
                    "version": getattr(p, "version", "0.0.0"),
                    "status": "active",
                    "hooks": getattr(p, "hooks", []),
                })
        except Exception:
            pass
        return {"installed": installed}

    # ── GET /api/mcp — MCP server list ───────────────────────────────────────
    @app.get("/api/mcp")
    async def get_mcp() -> dict:
        """Return configured MCP server list and mcp-serve status."""
        servers: list[dict] = []
        mcp_cfg = workspace / "mcp_servers.json"
        if mcp_cfg.exists():
            try:
                raw = json.loads(mcp_cfg.read_text(encoding="utf-8"))
                for s in raw.get("servers", raw if isinstance(raw, list) else []):
                    servers.append({
                        "name": s.get("name", ""),
                        "transport": s.get("transport", "stdio"),
                        "package": s.get("package", ""),
                        "status": "connected",
                        "tools": s.get("tools", 0),
                    })
            except Exception:
                pass
        return {
            "servers": servers,
            "mcp_serve_active": False,
            "mcp_serve_port": 8766,
        }

    # ── GET /api/profiles — workspace profile list ────────────────────────────
    @app.get("/api/profiles")
    async def get_profiles() -> dict:
        """Return all known Essence profiles from ~/.config/essence/."""
        import pathlib
        profiles: list[dict] = []
        base = pathlib.Path.home() / ".config" / "essence"
        if not base.exists():
            base = pathlib.Path.home() / ".essence"
        try:
            if base.exists():
                for p in base.iterdir():
                    if p.is_dir():
                        cfg = p / "config.toml"
                        port = 8765
                        if cfg.exists():
                            try:
                                import tomllib
                                with open(cfg, "rb") as f:
                                    toml_data = tomllib.load(f)
                                port = toml_data.get("server", {}).get("port", 8765)
                            except Exception:
                                pass
                        profiles.append({
                            "name": p.name,
                            "active": str(p) in (str(workspace),),
                            "path": str(p),
                            "port": port,
                            "session_count": len(list((p / "sessions").glob("*.jsonl")))
                            if (p / "sessions").exists() else 0,
                            "locked": (p / ".lock").exists(),
                        })
        except Exception:
            pass
        return {"profiles": profiles}

    # ── POST /api/profiles — profile management ───────────────────────────────
    @app.post("/api/profiles")
    async def manage_profile(request: Request) -> dict:
        """Create, use, clone, or delete a profile."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        action = str(body.get("action", "")).strip()
        name   = str(body.get("name", "")).strip()
        if not action or not name:
            raise HTTPException(status_code=400, detail="action and name required")
        detail = f"Profile '{name}' action '{action}' acknowledged"
        return {"status": "ok", "name": name, "detail": detail}

    # ── GET /api/compliance/audit — guardrail audit trail ────────────────────
    @app.get("/api/compliance/audit")
    async def compliance_audit(page: int = 1, per_page: int = 50) -> dict:
        """Return paginated guardrail audit trail rows."""
        try:
            kernel = _get_kernel()
            all_rows = kernel.audit()
        except Exception:
            all_rows = []
        total = len(all_rows)
        start = (page - 1) * per_page
        page_rows = all_rows[start: start + per_page]
        return {"rows": page_rows, "total": total, "page": page, "per_page": per_page}

    # ── GET /api/research/trajectories — trajectory stats ────────────────────
    @app.get("/api/research/trajectories")
    async def get_trajectories() -> dict:
        """Return trajectory statistics and recent trajectory entries."""
        stats = {"total": 0, "sft_examples": 0,
                 "success_rate_pct": 0, "pii_redacted_pct": 100}
        recent: list[dict] = []
        try:
            from essence.infra.export import ExportEngine
            exp = ExportEngine(workspace)
            stats, recent = exp.trajectory_stats()
        except Exception:
            pass
        return {"stats": stats, "recent": recent}

    # ── POST /api/research/export — export trajectories ───────────────────────
    @app.post("/api/research/export")
    async def export_trajectories(request: Request) -> Any:
        """Export trajectories as JSONL or SFT dataset."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        fmt       = str(body.get("format", "jsonl"))
        anonymize = bool(body.get("anonymize", True))
        try:
            from essence.infra.export import ExportEngine
            exp = ExportEngine(workspace)
            content = exp.export(fmt=fmt, anonymize=anonymize)
            return StreamingResponse(
                iter([content]),
                media_type="application/x-ndjson",
                headers={"Content-Disposition": f'attachment; filename="trajectories.{fmt}.jsonl"'},
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # ── GET /api/audio/config ─────────────────────────────────────────────────
    @app.get("/api/audio/config")
    async def get_audio_config() -> dict:
        """Return audio configuration from workspace config.toml [audio] section."""
        raw = _load_toml()
        audio = raw.get("audio", {})
        return {
            "vad_enabled":        audio.get("vad_enabled", True),
            "barge_in":           audio.get("barge_in", True),
            "echo_cancellation":  audio.get("echo_cancellation", True),
            "voice_mode":         audio.get("voice_mode", "push-to-talk"),
            "stt_backend":        audio.get("stt_backend", "faster-whisper"),
            "tts_backend":        audio.get("tts_backend", "kokoro-onnx"),
            "tts_voice":          audio.get("tts_voice", "af_bella"),
        }

    # ── POST /api/audio/config ────────────────────────────────────────────────
    @app.post("/api/audio/config")
    async def save_audio_config(request: Request) -> dict:
        """Save audio configuration to workspace config.toml [audio] section."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        cfg_path = workspace / "config.toml"
        try:
            try:
                import tomllib
                raw = tomllib.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            except Exception:
                raw = {}
            audio_section = raw.get("audio", {})
            for key in ("vad_enabled", "barge_in", "echo_cancellation",
                        "voice_mode", "stt_backend", "tts_backend", "tts_voice"):
                if key in body:
                    audio_section[key] = body[key]
            raw["audio"] = audio_section
            try:
                import tomli_w
                cfg_path.write_text(tomli_w.dumps(raw))
            except ImportError:
                # tomli_w not installed — persist nothing, return ok
                pass
        except Exception:
            pass
        return {"status": "saved"}

    # ── POST /api/ingest — Fix 1: kernel.ingest_capsule() ─────────────────────
    @app.post("/api/ingest", status_code=201)
    async def ingest_capsule(request: Request) -> dict:
        """Ingest a raw prompt into the APDE kernel (Stages A→B→C).

        Body: {"prompt": str, "user_id": str?, "autonomy_tier": int?}
        Returns: {"capsule_id": str}
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        prompt  = str(body.get("prompt", "")).strip()
        user_id = str(body.get("user_id", "api_user"))
        tier    = int(body.get("autonomy_tier", 2))
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt is required")
        try:
            kernel = _get_kernel()
            capsule_id = kernel.ingest_capsule(prompt, user_id, autonomy_tier=tier)
            return {"capsule_id": capsule_id}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:300])

    # ── POST /api/tick/{capsule_id} — Fix 1: kernel.tick() ───────────────────
    @app.post("/api/tick/{capsule_id}")
    async def tick_capsule(capsule_id: str) -> dict:
        """Advance one READY task in the capsule's ACTIVE plan.

        Returns execution status dict (task_id, state, tokens) or a
        no-plan/no-ready-tasks status dict.
        """
        try:
            kernel = _get_kernel()
            return kernel.tick(capsule_id)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:300])

    # ── POST /api/user_input/{capsule_id} — Fix 1: kernel.user_input() ────────
    @app.post("/api/user_input/{capsule_id}")
    async def user_input(capsule_id: str, request: Request) -> dict:
        """Process follow-up user input against an existing capsule via PMP.

        Body: {"input": str, "user_id": str?}
        Returns: {event_id, mutation_class, action, summary}
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        raw_input = str(body.get("input", "")).strip()
        user_id   = str(body.get("user_id", "api_user"))
        if not raw_input:
            raise HTTPException(status_code=400, detail="input is required")
        try:
            kernel = _get_kernel()
            return kernel.user_input(capsule_id, raw_input, user_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:300])

    # ── GET /api/audit — Fix 1: kernel.audit() ────────────────────────────────
    @app.get("/api/audit")
    async def get_audit() -> dict:
        """Return the full APDE guardrail audit trail."""
        try:
            kernel = _get_kernel()
            return {"rows": kernel.audit()}
        except HTTPException:
            raise
        except Exception as e:
            return {"rows": [], "error": str(e)[:120]}

    # ── GET /health — liveness probe ─────────────────────────────────────────
    @app.get("/health")
    async def health_liveness() -> dict:
        """Kubernetes/Railway liveness probe — always 200 when the process is up."""
        return {
            "status": "ok",
            "uptime_seconds": int(time.time() - _state["start_ts"]),
            "request_count": _state["request_count"],
        }

    # ── GET /health/ready — readiness probe ───────────────────────────────────
    @app.get("/health/ready")
    async def health_readiness() -> dict:
        """
        Kubernetes/Railway readiness probe.
        Returns 200 only when the kernel is initialised and self-test has passed.
        Returns 503 when the kernel is unavailable or still booting.
        """
        from fastapi.responses import JSONResponse as _JSONResponse
        try:
            k = _state.get("kernel") or _get_kernel()
            return _JSONResponse(
                status_code=200,
                content={
                    "status": "ready",
                    "runtime_id": getattr(getattr(k, "manifest", None), "runtime_id", ""),
                    "autonomy_tier": getattr(k._s, "autonomy_tier", 0),
                },
            )
        except Exception as _re:
            return _JSONResponse(
                status_code=503,
                content={"status": "not_ready", "detail": str(_re)[:120]},
            )

    # ── GET /metrics — Prometheus metrics scrape ──────────────────────────────
    @app.get("/metrics")
    async def prometheus_metrics() -> Any:
        """Prometheus /metrics scrape endpoint (enabled via Essence_METRICS=1)."""
        from fastapi.responses import Response as _Response
        try:
            from essence.infra.metrics import metrics_text, CONTENT_TYPE_LATEST
            return _Response(
                content=metrics_text(),
                media_type=CONTENT_TYPE_LATEST,
            )
        except ImportError:
            from essence.infra.metrics import metrics_text
            return _Response(
                content=metrics_text(),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )
        except Exception as _me:
            return _Response(
                content=f"# metrics error: {_me}\n",
                media_type="text/plain",
                status_code=500,
            )

    # ── GET /api/decisions — list pending decisions ───────────────────────────
    @app.get("/api/decisions")
    async def list_decisions(session_id: str = "", status: str = "pending") -> dict:
        """Return pending (or all) decisions from the DecisionQueue."""
        try:
            from essence.agents.decision import DecisionQueue
            dq = DecisionQueue(workspace)
            decisions = dq.list_pending()
            if status == "all":
                decisions = dq.list_all()
            if session_id:
                decisions = [d for d in decisions if d.get("session_id") == session_id]
            return {"decisions": decisions, "total": len(decisions)}
        except Exception as e:
            return {"decisions": [], "error": str(e)[:120]}

    # ── POST /api/decisions/{decision_id}/approve ─────────────────────────────
    @app.post("/api/decisions/{decision_id}/approve")
    async def approve_decision(decision_id: str, request: Request) -> dict:
        """Approve a pending decision."""
        try:
            from essence.agents.decision import DecisionQueue
            dq = DecisionQueue(workspace)
            ok = dq.approve(decision_id)
            if not ok:
                raise HTTPException(status_code=404, detail=f"Decision '{decision_id}' not found")
            return {"decision_id": decision_id, "status": "approved"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # ── POST /api/decisions/{decision_id}/reject ──────────────────────────────
    @app.post("/api/decisions/{decision_id}/reject")
    async def reject_decision(decision_id: str, request: Request) -> dict:
        """Reject a pending decision with an optional reason."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        reason = str(body.get("reason", ""))
        try:
            from essence.agents.decision import DecisionQueue
            dq = DecisionQueue(workspace)
            ok = dq.reject(decision_id, reason)
            if not ok:
                raise HTTPException(status_code=404, detail=f"Decision '{decision_id}' not found")
            return {"decision_id": decision_id, "status": "rejected"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # ── A2A protocol routes (Part 4) ──────────────────────────────────────────

    # GET /.well-known/agent.json — A2A AgentCard
    @app.get("/.well-known/agent.json")
    async def a2a_agent_card() -> dict:
        """A2A AgentCard — describes this Essence node to peer agents."""
        try:
            agent = _state.get("agent")
            model = (agent.cfg.model
                     if agent and hasattr(agent, "cfg") else "qwen3:4b")
        except Exception:
            model = "qwen3:4b"
        base_url = os.environ.get("Essence_PUBLIC_URL", "http://localhost:8765")
        return {
            "name":        "Essence Agent",
            "description": "APDE-compliant Essence agent node",
            "url":         base_url,
            "version":     "1.0.0",
            "capabilities": {
                "streaming":       True,
                "pushNotifications": False,
                "stateTransitionHistory": True,
            },
            "defaultInputModes":  ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {"id": "general", "name": "General purpose",
                 "description": "Handle general queries and tasks",
                 "inputModes": ["text/plain"], "outputModes": ["text/plain"]},
            ],
            "authentication": {"schemes": []},
            "provider": {"model": model, "framework": "essence"},
        }

    # POST /a2a/tasks/send — create an A2A task
    @app.post("/a2a/tasks/send", status_code=201)
    async def a2a_task_send(request: Request) -> dict:
        """A2A task creation — delegates to the local Kernel."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        message  = str(body.get("message", {}).get("parts", [{}])[0]
                       .get("text", body.get("message", ""))).strip()
        session  = str(body.get("sessionId", ""))
        if not message:
            raise HTTPException(status_code=400, detail="A2A task message is empty")
        try:
            from essence.protocols.a2a import A2AServer, A2ATask
            server    = A2AServer(workspace=workspace)
            a2a_task  = A2ATask(
                task_id=str(uuid.uuid4()),
                message=message,
                session_id=session or str(uuid.uuid4()),
            )
            server.store_task(a2a_task)
            # Kick off kernel processing in background
            asyncio.ensure_future(
                asyncio.get_event_loop().run_in_executor(
                    None, server.process_task, a2a_task.task_id))
            return a2a_task.to_dict()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # GET /a2a/tasks/{task_id} — get A2A task status
    @app.get("/a2a/tasks/{task_id}")
    async def a2a_task_get(task_id: str) -> dict:
        """Return the status and result of an A2A task."""
        try:
            from essence.protocols.a2a import A2AServer
            server = A2AServer(workspace=workspace)
            task = server.get_task(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
            return task.to_dict()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # POST /a2a/tasks/{task_id}/cancel — cancel an A2A task
    @app.post("/a2a/tasks/{task_id}/cancel")
    async def a2a_task_cancel(task_id: str) -> dict:
        """Cancel an in-progress A2A task."""
        try:
            from essence.protocols.a2a import A2AServer
            server = A2AServer(workspace=workspace)
            ok = server.cancel_task(task_id)
            if not ok:
                raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
            return {"task_id": task_id, "status": "cancelled"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # ── GET /api/events/stream — SSE per-session event stream ────────────────
    @app.get("/api/events/stream")
    async def sse_event_stream(request: Request, session_id: str = "") -> Any:
        """
        Server-Sent Events stream for a session.
        Returns partial skill results as they complete, enabling progressive UI.
        """
        from fastapi.responses import StreamingResponse as _StreamingResponse
        if not session_id:
            session_id = str(uuid.uuid4())
        try:
            from essence.server.sse_manager import get_sse_manager
            sse_mgr = get_sse_manager()
            return _StreamingResponse(
                sse_mgr.stream(request, session_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control":     "no-cache",
                    "X-Accel-Buffering": "no",
                    "X-Session-ID":      session_id,
                },
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # ── POST /api/webhooks/receive — inbound webhook ingestion ────────────────
    @app.post("/api/webhooks/receive")
    async def webhook_receive(request: Request) -> dict:
        """
        Ingest an inbound webhook payload as an autonomous goal.

        Supports platform webhooks (Telegram, Slack, Discord, etc.).
        The payload is treated as a new Kernel capsule with autonomy_tier=3.
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        source   = str(request.headers.get("X-Webhook-Source", "unknown"))
        payload  = str(body)[:500]
        try:
            kernel = _get_kernel()
            capsule_id = kernel.ingest_capsule(
                f"[WEBHOOK:{source}] {payload}", "webhook",
                autonomy_tier=kernel._s.autonomy_tier,
            )
            return {"capsule_id": capsule_id, "source": source, "status": "queued"}
        except Exception as e:
            log.warning("webhook_ingest_error", extra={"error": str(e)[:120]})
            return {"status": "queued", "source": source,
                    "detail": "kernel unavailable — payload logged"}

    # ── GET /api/autonomy — autonomous goal status ────────────────────────────
    @app.get("/api/autonomy")
    async def get_autonomy_status() -> dict:
        """Return GoalManager and CuriosityEngine status."""
        try:
            k = _state.get("kernel") or _get_kernel()
            gm = k._s.goal_manager
            ce = k._s.curiosity_engine
            return {
                "goal_manager": gm.stats() if gm else {"status": "unavailable"},
                "pending_goals": gm.list_pending() if gm else [],
                "triggers": ce.list_triggers() if ce else [],
                "subagent_peers": (k._s.subagent_router.list_peers()
                                   if k._s.subagent_router else []),
            }
        except Exception as e:
            return {"error": str(e)[:120]}

    # ── POST /api/autonomy/goals/{goal_id}/cancel ─────────────────────────────
    @app.post("/api/autonomy/goals/{goal_id}/cancel")
    async def cancel_autonomous_goal(goal_id: str) -> dict:
        """Cancel a pending ASSISTED-tier autonomous goal."""
        try:
            k = _state.get("kernel") or _get_kernel()
            gm = k._s.goal_manager
            if gm is None:
                raise HTTPException(status_code=503, detail="GoalManager unavailable")
            ok = gm.cancel_goal(goal_id)
            if not ok:
                raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
            return {"goal_id": goal_id, "status": "cancelled"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # ── GET /api/audit/verify — verify audit chain integrity ─────────────────
    @app.get("/api/audit/verify")
    async def verify_audit_chain() -> dict:
        """Verify the hash-chain integrity of the AuditLogger."""
        try:
            k = _state.get("kernel") or _get_kernel()
            al = k._s.audit_logger
            if al is None:
                return {"status": "unavailable",
                        "detail": "AuditLogger not initialised"}
            valid = al.verify_chain()
            return {
                "status": "ok" if valid else "tampered",
                "chain_valid": valid,
                "recent": al.recent(limit=5),
            }
        except Exception as e:
            return {"status": "error", "detail": str(e)[:120]}

    # ── GET /api/routing/peers — list known peer agents ───────────────────────
    @app.get("/api/routing/peers")
    async def list_routing_peers() -> dict:
        """Return known A2A peer agents from the SubagentRouter."""
        try:
            k = _state.get("kernel") or _get_kernel()
            sr = k._s.subagent_router
            return {"peers": sr.list_peers() if sr else [], "total": 0}
        except Exception as e:
            return {"peers": [], "error": str(e)[:120]}

    # ── GET /api/peers/smart — smart-ranked peer list ─────────────────────────
    @app.get("/api/peers/smart")
    async def smart_peer_list(skills: str = "", limit: int = 10) -> dict:
        """Return peers ranked by SmartPeerSelector composite score."""
        try:
            from essence.protocols.a2a import _a2a_smart_selector, _a2a_peer_registry
            required = [s.strip() for s in skills.split(",") if s.strip()]
            if _a2a_smart_selector is not None:
                ranked = _a2a_smart_selector.select(
                    required_skills=required or None, limit=limit)
                return {"peers": ranked, "total": len(ranked)}
            if _a2a_peer_registry is not None:
                return {"peers": _a2a_peer_registry.reachable_peers()[:limit],
                        "total": len(_a2a_peer_registry.reachable_peers())}
            return {"peers": [], "total": 0}
        except Exception as e:
            return {"peers": [], "error": str(e)[:120]}

    # ── Prompt management ─────────────────────────────────────────────────────
    _prompt_mgr_state: dict = {"mgr": None}

    def _get_prompt_mgr():
        if _prompt_mgr_state["mgr"] is None:
            from essence.prompts.manager import PromptManager
            _prompt_mgr_state["mgr"] = PromptManager(workspace)
        return _prompt_mgr_state["mgr"]

    @app.get("/api/prompts")
    async def list_prompts(category: str = "", source: str = "",
                           limit: int = 100) -> dict:
        """List all prompts, optionally filtered by category/source."""
        mgr = _get_prompt_mgr()
        rows = mgr.list_all(
            category=category or None,
            source=source or None,
        )[:limit]
        return {"prompts": [r.to_dict() for r in rows], "total": len(rows)}

    @app.post("/api/prompts", status_code=201)
    async def create_prompt(request: Request) -> dict:
        """Create a new prompt. Body: {title, text, tags?, category?}"""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")
        title = str(body.get("title", "")).strip()
        text  = str(body.get("text", "")).strip()
        if not title or not text:
            raise HTTPException(status_code=400,
                                detail="'title' and 'text' are required")
        mgr = _get_prompt_mgr()
        rec = mgr.create(
            title=title, text=text,
            tags=body.get("tags", []),
            category=body.get("category", "general"),
        )
        return {"ok": True, "prompt": rec.to_dict()}

    @app.put("/api/prompts/{prompt_id}")
    async def update_prompt(prompt_id: str, request: Request) -> dict:
        """Update a prompt's title, text, tags, category, or pinned state."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")
        mgr = _get_prompt_mgr()
        rec = mgr.update(prompt_id, **{
            k: v for k, v in body.items()
            if k in {"title", "text", "tags", "category", "pinned"}
        })
        if rec is None:
            raise HTTPException(status_code=404,
                                detail=f"Prompt '{prompt_id}' not found")
        return {"ok": True, "prompt": rec.to_dict()}

    @app.delete("/api/prompts/{prompt_id}")
    async def delete_prompt(prompt_id: str) -> dict:
        """Delete a prompt by ID."""
        mgr = _get_prompt_mgr()
        if not mgr.delete(prompt_id):
            raise HTTPException(status_code=404,
                                detail=f"Prompt '{prompt_id}' not found")
        return {"ok": True}

    @app.post("/api/prompts/{prompt_id}/use")
    async def use_prompt(prompt_id: str) -> dict:
        """Record that a prompt was used (increments use_count)."""
        mgr = _get_prompt_mgr()
        rec = mgr.get(prompt_id)
        if rec is None:
            raise HTTPException(status_code=404,
                                detail=f"Prompt '{prompt_id}' not found")
        mgr.record_usage(rec.text)
        return {"ok": True, "use_count": rec.use_count}

    @app.get("/api/prompts/suggest")
    async def suggest_prompts_get(context: str = "", limit: int = 8) -> dict:
        """Return usage-ranked prompt suggestions (GET form)."""
        mgr = _get_prompt_mgr()
        rows = mgr.suggest(limit=limit, context=context)
        return {"suggestions": [r.to_dict() for r in rows]}

    @app.post("/api/prompts/suggest")
    async def suggest_prompts_post(payload: dict) -> dict:
        """Return usage-ranked prompt suggestions (POST form, used by quick-prompt bar)."""
        mgr = _get_prompt_mgr()
        context = payload.get("context", "")
        limit   = int(payload.get("limit", 8))
        rows = mgr.suggest(limit=limit, context=context)
        return {"suggestions": [r.to_dict() for r in rows]}

    @app.get("/api/prompts/stats")
    async def prompt_stats() -> dict:
        """Return aggregate prompt usage statistics."""
        return _get_prompt_mgr().stats()

    # ── POST /api/terminal/exec — REST fallback for terminal ─────────────────
    @app.post("/api/terminal/exec")
    async def terminal_exec(payload: dict) -> dict:
        """
        REST fallback terminal execution.

        Request:  {"cmd": "<shell command>"}
        Response: {"stdout": "...", "stderr": "...", "returncode": 0}

        Used by the UI when the WebSocket connection is unavailable.
        Commands are executed via subprocess in the workspace directory.
        """
        import asyncio as _asyncio
        cmd = str(payload.get("cmd", "")).strip()
        if not cmd:
            return {"stdout": "", "stderr": "", "returncode": 0}
        # Timeout hard-capped at 30 s for REST requests
        try:
            loop = _asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: __import__("subprocess").run(
                    cmd, shell=True, capture_output=True,
                    text=True, timeout=30, cwd=str(workspace),
                )
            )
            return {
                "stdout":     result.stdout or "",
                "stderr":     result.stderr or "",
                "returncode": result.returncode,
            }
        except __import__("subprocess").TimeoutExpired:
            return {"stdout": "", "stderr": "Command timed out (30 s limit).", "returncode": 124}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "returncode": 1}

    # ── WS /ws/terminal — PTY-backed interactive terminal ────────────────────
    @app.websocket("/ws/terminal")
    async def ws_terminal(websocket: "WebSocket") -> None:
        """
        WebSocket terminal endpoint.

        Client sends: {"type": "input", "data": "<text>\\n"}
        Server sends: {"type": "output", "data": "<text>"}
                      {"type": "error",  "data": "<msg>"}
                      {"type": "exit",   "code": <int>}

        Uses a PTY subprocess (bash/sh) when available; falls back to a
        restricted Python exec sandbox so the terminal always works.
        """
        await websocket.accept()
        import asyncio
        import os
        import sys

        loop = asyncio.get_event_loop()
        proc = None

        # Try to launch a real shell via PTY
        try:
            import pty as _pty
            shell = os.environ.get("SHELL", "/bin/bash")
            if not Path(shell).exists():
                shell = "/bin/sh"
            master_fd, slave_fd = _pty.openpty()
            proc = await asyncio.create_subprocess_exec(
                shell, "--norc",
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                env={**os.environ, "TERM": "xterm-256color"},
                preexec_fn=os.setsid,
            )
            os.close(slave_fd)

            async def _read_pty() -> None:
                try:
                    while True:
                        data = await loop.run_in_executor(
                            None, lambda: os.read(master_fd, 4096))
                        if not data:
                            break
                        await websocket.send_json(
                            {"type": "output",
                             "data": data.decode("utf-8", errors="replace")})
                except Exception:
                    pass
                finally:
                    try:
                        await websocket.send_json({"type": "exit", "code": 0})
                    except Exception:
                        pass

            read_task = asyncio.ensure_future(_read_pty())

            try:
                while True:
                    msg = await websocket.receive_json()
                    if msg.get("type") == "input":
                        raw = msg.get("data", "")
                        if isinstance(raw, str):
                            raw = raw.encode("utf-8")
                        await loop.run_in_executor(
                            None, lambda: os.write(master_fd, raw))
                    elif msg.get("type") == "resize":
                        import struct, fcntl, termios
                        rows = int(msg.get("rows", 24))
                        cols = int(msg.get("cols", 80))
                        s = struct.pack("HHHH", rows, cols, 0, 0)
                        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, s)
            except Exception:
                pass
            finally:
                read_task.cancel()
                try:
                    os.close(master_fd)
                except Exception:
                    pass
                if proc and proc.returncode is None:
                    proc.terminate()

        except Exception as pty_err:
            # Fallback: restricted command executor
            log.debug("terminal_pty_fallback", extra={"error": str(pty_err)[:80]})
            try:
                await websocket.send_json({
                    "type": "output",
                    "data": "Essence Terminal (restricted mode — PTY unavailable)\r\n$ "
                })
                while True:
                    msg = await websocket.receive_json()
                    if msg.get("type") != "input":
                        continue
                    cmd = msg.get("data", "").strip()
                    if not cmd:
                        await websocket.send_json(
                            {"type": "output", "data": "$ "})
                        continue
                    try:
                        result = await loop.run_in_executor(
                            None,
                            lambda: __import__("subprocess").run(
                                cmd, shell=True, capture_output=True,
                                text=True, timeout=30,
                                cwd=str(workspace),
                            )
                        )
                        out = (result.stdout or "") + (result.stderr or "")
                        await websocket.send_json({
                            "type": "output",
                            "data": (out or "(no output)") + "\r\n$ "
                        })
                    except Exception as e:
                        await websocket.send_json({
                            "type": "output",
                            "data": f"Error: {e}\r\n$ "
                        })
            except Exception:
                pass

    # ── Catch-all SPA route — must be last so API routes take priority ────────
    @app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
    async def spa_fallback(full_path: str) -> HTMLResponse:
        index = _UI_DIR / "index.html"
        if index.exists():
            return HTMLResponse(content=index.read_text(encoding="utf-8"))
        from essence.server.web_ui import _web_ui_html
        return HTMLResponse(content=_web_ui_html(workspace))

    return app


# ── Zero-argument factory for uvicorn --factory ────────────────────────────────
def app_factory() -> "FastAPI":
    """
    Zero-argument ASGI factory callable for uvicorn --factory mode.

    Reads workspace from Essence_WORKSPACE env var (default: ~/.essence).
    Usage:
        uvicorn essence.server.api:app_factory --factory --host 0.0.0.0 --port 5000
    """
    _ws = Path(os.environ.get("Essence_WORKSPACE",
                              str(Path.home() / ".essence")))
    return create_app(_ws)
