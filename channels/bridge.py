"""SystemBridge (orchestrator / worker / intermediary topology). CANONICAL."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.conn import get_async_client  # noqa: F401

# SYSTEM BRIDGE  (orchestrator / worker / intermediary topology)
# ══════════════════════════════════════════════════════════════════════════════
#
#  SystemBridge makes Essence fully topology-aware so it can:
#
#  ORCHESTRATOR — fan-out tasks to one or more worker nodes over HTTP, collect
#            results, merge and return to the local agent.
#            (env alias: Essence_ROLE=master still accepted for backward compat)
#
#  WORKER  — exposes /api/task and /api/chat endpoints that an orchestrator
#            can call.  On startup, registers itself with Essence_ORCH_URL if
#            set.  (env alias: Essence_ROLE=slave still accepted for backward compat)
#
#  INTERMEDIARY — translates between protocols: accepts requests in format A
#            (e.g. OpenAI /v1/chat/completions), routes them to the local
#            provider chain, and returns the response in format A.  This lets
#            any tool that speaks OpenAI talk to any local Essence backend
#            with zero changes to the external tool.
#
#  STANDALONE — default; bridge is inert, no extra network endpoints.
#
#  Env vars:
#    Essence_ROLE        = orchestrator | worker | intermediary | standalone
#                          (legacy values master | slave are accepted and mapped)
#    Essence_ORCH_URL    = http://orchestrator-node:7860    (worker → registration)
#    Essence_MASTER_URL  = same as Essence_ORCH_URL         (legacy alias)
#    Essence_WORKER_URLS = http://node1:7860,http://node2:7860  (orchestrator)
#    Essence_SLAVE_URLS  = same as Essence_WORKER_URLS      (legacy alias)
#    Essence_WORKER_TOKEN = shared bearer token for worker auth
#    Essence_SLAVE_TOKEN  = same as Essence_WORKER_TOKEN    (legacy alias)

class SystemBridge:
    """
    Plug-and-play multi-node system adapter.

    Usage — attach to your Agent after construction:

        bridge = SystemBridge(hw, ws)
        agent._bridge = bridge
        bridge.start()          # registers w/ master, opens slave endpoints, etc.

    The bridge is completely inert when role=STANDALONE — zero overhead.
    """

    def __init__(self, hw: "HardwareProfile", ws: Path,
                 role: SystemRole | None = None) -> None:
        self.hw   = hw
        self.ws   = ws
        if role is not None:
            self.role = role
        else:
            # Normalise Essence_ROLE: accept legacy "slave"/"master" values
            _role_raw = os.environ.get("Essence_ROLE", "standalone").lower()
            if _role_raw == "slave":
                _role_raw = "worker"   # backward compat — deprecated
            elif _role_raw == "master":
                _role_raw = "orchestrator"  # backward compat — deprecated
            # Fall back to get_system_role() for URL-based auto-detection
            try:
                self.role = SystemRole(_role_raw)
            except ValueError:
                self.role = get_system_role()
        # Token: prefer new name, fall back to legacy alias
        self._token = (os.environ.get("Essence_WORKER_TOKEN")
                       or os.environ.get("Essence_SLAVE_TOKEN", ""))
        # Worker URLs: prefer new name, fall back to legacy alias
        _worker_urls_raw = (os.environ.get("Essence_WORKER_URLS")
                            or os.environ.get("Essence_SLAVE_URLS", ""))
        self._worker_urls: list[str] = [
            u.strip() for u in _worker_urls_raw.split(",") if u.strip()
        ]
        # Orchestrator URL: prefer new name, fall back to legacy alias
        self._orch_url = (
            os.environ.get("Essence_ORCH_URL")
            or os.environ.get("Essence_MASTER_URL", "")
        ).rstrip("/")
        self._running = False

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self.role == SystemRole.WORKER and self._orch_url:
            self._register_with_orchestrator()
        self._running = True
        log.info("system_bridge_started", extra={
            "role": self.role.value,
            "worker_count": len(self._worker_urls),
        })

    def stop(self) -> None:
        self._running = False

    # ── Orchestrator: delegate a task to worker nodes (round-robin) ───────────
    def delegate_task(self, task: str, timeout: int = 120) -> str:
        """
        Fan-out ``task`` to available worker nodes in round-robin order.
        Falls back to local execution if all workers fail.
        """
        if self.role != SystemRole.ORCHESTRATOR or not self._worker_urls:
            return ""   # caller will run locally
        errors: list[str] = []
        for url in self._worker_urls:
            try:
                payload = json.dumps({"task": task}).encode()
                req = urllib.request.Request(
                    f"{url}/api/task", data=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {self._token}"},
                    method="POST")
                resp = json.loads(
                    urllib.request.urlopen(req, timeout=timeout)
                    .read().decode("utf-8", errors="replace"))
                return resp.get("result", "")
            except Exception as e:
                errors.append(f"{url}: {e}")
        log.warning("delegate_task_all_workers_failed",
                    extra={"errors": errors})
        return ""   # signal caller to run locally

    async def adelegate_task(self, task: str, timeout: int = 120) -> str:
        """Async version of delegate_task()."""
        if self.role != SystemRole.ORCHESTRATOR or not self._worker_urls:
            return ""

        client = get_async_client()
        for url in self._worker_urls:
            try:
                payload = {"task": task}
                headers = {"Authorization": f"Bearer {self._token}"}
                if client:
                    resp = await client.post(f"{url}/api/task", json=payload, headers=headers, timeout=timeout)
                    return resp.json().get("result", "")
                else:
                    # Thread fallback
                    loop = asyncio.get_running_loop()
                    return await loop.run_in_executor(None, self.delegate_task, task, timeout)
            except Exception:
                continue
        return ""

    def delegate_chat(self, message: str, model: str = "",
                      timeout: int = 60) -> str:
        """Delegate a chat turn to the first available worker."""
        if self.role != SystemRole.ORCHESTRATOR or not self._worker_urls:
            return ""
        for url in self._worker_urls:
            try:
                payload = json.dumps({"message": message,
                                      "model": model}).encode()
                req = urllib.request.Request(
                    f"{url}/api/chat", data=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {self._token}"},
                    method="POST")
                resp = json.loads(
                    urllib.request.urlopen(req, timeout=timeout)
                    .read().decode("utf-8", errors="replace"))
                return resp.get("response", "")
            except Exception:
                continue
        return ""

    async def adelegate_chat(self, message: str, model: str = "",
                            timeout: int = 60) -> str:
        """Async version of delegate_chat()."""
        if self.role != SystemRole.ORCHESTRATOR or not self._worker_urls:
            return ""

        client = get_async_client()
        for url in self._worker_urls:
            try:
                payload = {"message": message, "model": model}
                headers = {"Authorization": f"Bearer {self._token}"}
                if client:
                    resp = await client.post(f"{url}/api/chat", json=payload, headers=headers, timeout=timeout)
                    return resp.json().get("response", "")
                else:
                    loop = asyncio.get_running_loop()
                    return await loop.run_in_executor(None, self.delegate_chat, message, model, timeout)
            except Exception:
                continue
        return ""

    # ── Worker: register self with orchestrator ───────────────────────────────
    def _register_with_orchestrator(self) -> None:
        try:
            import socket
            my_ip   = socket.gethostbyname(socket.gethostname())
            my_port = int(os.environ.get("Essence_PORT", "7860"))
            payload = json.dumps({
                "url":   f"http://{my_ip}:{my_port}",
                "tier":  self.hw.tier,
                "model": self.hw.model,
            }).encode()
            req = urllib.request.Request(
                f"{self._orch_url}/api/workers/register",
                data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self._token}"},
                method="POST")
            urllib.request.urlopen(req, timeout=10)
            log.info("worker_registered_with_orchestrator",
                     extra={"orchestrator": self._orch_url})
        except Exception as e:
            log.warning("worker_registration_failed",
                        extra={"error": str(e)})

    # backward-compat alias used by pre-rename call sites
    _register_with_master = _register_with_orchestrator

    # ── Intermediary: OpenAI-compat proxy ────────────────────────────────────
    def make_openai_proxy_handler(self, provider: Any, model: str
                                  ) -> Callable[[dict], dict]:
        """
        Returns a request handler that accepts an OpenAI /v1/chat/completions
        body, routes it through the local ProviderChain, and returns an
        OpenAI-compat response dict.

        Plug this into any WSGI/ASGI framework for an intermediary node:

            @app.post("/v1/chat/completions")
            async def proxy(req: dict):
                return bridge.make_openai_proxy_handler(provider, model)(req)
        """
        def _handler(body: dict) -> dict:
            msgs    = body.get("messages", [])
            stream  = body.get("stream", False)
            m       = body.get("model", model)
            tokens  = list(provider.complete(msgs, model=m,
                                              stream=False, thinking=False))
            content = "".join(tokens)
            return {
                "id":      f"essence-{int(time.time())}",
                "object":  "chat.completion",
                "model":   m,
                "choices": [{"index": 0, "message":
                              {"role": "assistant", "content": content},
                              "finish_reason": "stop"}],
                "usage":   {"prompt_tokens": 0, "completion_tokens": len(content)//4,
                            "total_tokens": len(content)//4},
            }
        return _handler




# ══════════════════════════════════════════════════════════════════════════════
