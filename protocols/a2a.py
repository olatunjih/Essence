"""A2A protocol +  peer registry + social layer."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.otel import inject_trace_headers  # noqa: F401  [real source bug: used in _headers() without import]

# A2A PROTOCOL
# ══════════════════════════════════════════════════════════════════════════════
# Implements a subset of the Google A2A (Agent-to-Agent) open protocol so
# Essence nodes can interoperate with any A2A-compliant agent system.
#
# Spec reference: https://google.github.io/A2A/spec/
#
# Two roles:
#   A2AServer — exposes this Essence node as an A2A-compatible agent endpoint
#               GET  /.well-known/agent.json  → AgentCard
#               POST /a2a/tasks/send          → create task
#               GET  /a2a/tasks/{id}          → get task status / result
#               POST /a2a/tasks/{id}/cancel   → cancel task
#
#   A2AClient — sends tasks to remote A2A-compatible agents
#               Wraps the remote HTTP calls and returns result text.
#
# Integration: A2AServer routes are registered into the FastAPI app in 
# The ORCHESTRATOR role uses A2AClient to delegate sub-tasks to peer nodes.

@_dc.dataclass
class A2ATask:
    """Represents a single A2A task (request + state)."""
    task_id:    str
    message:    str           # user-facing task description
    session_id: str           = ""
    status:     str           = "submitted"   # submitted|working|completed|failed|cancelled
    result:     str           = ""
    error:      str           = ""
    created_at: float         = _dc.field(default_factory=time.time)
    updated_at: float         = _dc.field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.task_id,
            "sessionId": self.session_id,
            "status": {"state": self.status},
            "history": [{"role": "user",   "parts": [{"text": self.message}]}]
                      + ([{"role": "agent", "parts": [{"text": self.result}]}]
                         if self.result else []),
            "error": self.error or None,
        }


class A2AServer:
    """
    A2A-compatible agent server mixin.

    Provides the AgentCard (capability manifest) and task routing
    endpoints that get mounted into the FastAPI app.

    Task persistence: tasks are written to <workspace>/logs/a2a_tasks.json
    on every mutation so a process restart does not silently drop pending
    delegated tasks.  Remote orchestrators polling for completion receive
    a 404 if the server restarts without persistence — this fixes that gap.
    """

    def __init__(self, hw: "HardwareProfile", workspace: Path) -> None:
        self._hw   = hw
        self._ws   = workspace
        self._tasks: dict[str, A2ATask] = {}
        self._lock  = threading.Lock()
        self._persist_path = workspace / "logs" / "a2a_tasks.json"
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_persisted()

    def _load_persisted(self) -> None:
        """Restore tasks that survived a process restart."""
        if not self._persist_path.exists():
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for d in raw:
                t = A2ATask(
                    task_id    = d["task_id"],
                    message    = d.get("message", ""),
                    session_id = d.get("session_id", ""),
                    status     = d.get("status", "submitted"),
                    result     = d.get("result", ""),
                    error      = d.get("error", ""),
                    created_at = d.get("created_at", time.time()),
                    updated_at = d.get("updated_at", time.time()),
                )
                self._tasks[t.task_id] = t
        except Exception:
            pass  # corrupt file — start fresh, do not crash

    def _persist(self) -> None:
        """Flush current task table to disk.  Called under self._lock."""
        try:
            rows = [
                {
                    "task_id":    t.task_id,
                    "message":    t.message,
                    "session_id": t.session_id,
                    "status":     t.status,
                    "result":     t.result,
                    "error":      t.error,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                }
                for t in self._tasks.values()
            ]
            self._persist_path.write_text(
                json.dumps(rows, indent=2), encoding="utf-8"
            )
        except Exception:
            pass  # persistence is best-effort — never block on write failure

    def _a2a_auth_block(self) -> dict:
        """Build the AAIF authentication block.
        OAuth2.1+PKCE when Essence_A2A_OAUTH=1; bearer when Essence_SLAVE_TOKEN set; else anonymous."""
        use_oauth = bool(os.environ.get("Essence_A2A_OAUTH"))
        oauth_server = os.environ.get("Essence_A2A_OAUTH_SERVER", "").rstrip("/")
        use_bearer = bool(os.environ.get("Essence_SLAVE_TOKEN"))
        if use_oauth and oauth_server:
            return {"authentication": {
                "schemes": ["oauth2"],
                "required": True,
                "oauth2": {
                    "authorizationUrl": f"{oauth_server}/authorize",
                    "tokenUrl":         f"{oauth_server}/token",
                    "pkce": True,
                    "scopes": {"agent:task": "Submit and manage agent tasks"},
                },
            }}
        return {"authentication": {
            "schemes": ["bearer"] if use_bearer else [],
            "required": use_bearer,
        }}

    def agent_card(self, base_url: str = "") -> dict:
        """Return the AAIF-compatible AgentCard manifest.

        Spec: https://www.aaif.ai/spec/agent-card (post-IBM ACP merge, Dec 2025)
        AAIF task states: submitted → working → input-required
                          → completed / failed / canceled
        """
        return {
            "name": "Essence",
            "description": (
                f"Essence Intelligence System v{Essence_VERSION} — "
                f"{self._hw.tier_label} — {self._hw.model}"),
            "url": base_url,
            "version": Essence_VERSION,
            # AAIF-required authentication block.
            # Set Essence_A2A_OAUTH=1 + Essence_A2A_OAUTH_SERVER=https://... for OAuth2.1+PKCE.
            # Falls back to bearer token (Essence_SLAVE_TOKEN) for internal ORCHESTRATOR→WORKER.
            **self._a2a_auth_block(),
            "capabilities": {
                "streaming": True,
                "pushNotifications": False,
                "stateTransitionHistory": True,
            },
            # AAIF-required input/output mode arrays
            "defaultInputModes":  ["text/plain", "audio/wav"],
            "defaultOutputModes": ["text/plain"],
            # AAIF skills array — each skill is a discrete capability advertisment
            "skills": [
                {"id": "chat",
                 "name": "Chat",
                 "description": "Multi-turn conversational AI with tool use",
                 "inputModes":  ["text/plain"],
                 "outputModes": ["text/plain"]},
                {"id": "agent_task",
                 "name": "Agent task",
                 "description": "Multi-step task execution (plan→execute→critique)",
                 "inputModes":  ["text/plain"],
                 "outputModes": ["text/plain"]},
                {"id": "data_analysis",
                 "name": "Data analysis",
                 "description": "EDA, forecasting, anomaly detection, A/B testing",
                 "inputModes":  ["text/plain"],
                 "outputModes": ["text/plain"]},
                {"id": "voice_transcribe",
                 "name": "Voice transcription",
                 "description": "Whisper STT — POST raw audio, receive text",
                 "inputModes":  ["audio/wav", "audio/webm"],
                 "outputModes": ["text/plain"]},
            ],
        }

    def create_task(self, message: str, session_id: str = "") -> A2ATask:
        """Create and register a new A2A task."""
        task = A2ATask(
            task_id    = f"a2a_{secrets.token_hex(8)}",
            message    = message,
            session_id = session_id or secrets.token_hex(4),
        )
        with self._lock:
            self._tasks[task.task_id] = task
            self._persist()
        return task

    def get_task(self, task_id: str) -> A2ATask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def update_task(self, task_id: str, status: str,
                    result: str = "", error: str = "") -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t:
                t.status     = status
                t.result     = result or t.result
                t.error      = error  or t.error
                t.updated_at = time.time()
                self._persist()
                # Fire-and-forget peer notifications on terminal states
                if status in ("completed", "failed", "canceled"):
                    _nb = _a2a_notify_bus  # module-level singleton set by _init_a2a_social
                    if _nb is not None:
                        _tok = os.environ.get("Essence_SLAVE_TOKEN", "")
                        _nb.notify_peers(t, token=_tok)

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t.status in ("submitted", "working"):
                t.status     = "canceled"   # AAIF spec: single-l spelling
                t.updated_at = time.time()
                self._persist()
                return True
            return False


class A2AClient:
    """
    A2A client — sends tasks to remote A2A-compatible agents.

    Used by the ORCHESTRATOR role to delegate sub-tasks to peer Essence
    nodes or any other A2A-compliant agent.

    Usage:
        client = A2AClient("http://worker-node:7860")
        result = client.send_task("Summarise all Python files in /src")
        print(result)
    """

    def __init__(self, base_url: str,
                 token: str = "",
                 timeout: float = 120.0) -> None:
        self._base    = base_url.rstrip("/")
        self._token   = token
        self._timeout = timeout

    def _headers(self) -> dict:
        h: dict = {"Content-Type": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        # v27: Inject W3C trace context for distributed tracing
        h = inject_trace_headers(h)
        return h

    def _post(self, path: str, body: dict) -> dict:
        """Synchronous POST. Uses httpx if available, else urllib."""
        url     = f"{self._base}{path}"
        payload = json.dumps(body).encode()
        if _HTTPX:
            resp = _httpx.post(url, content=payload,
                               headers=self._headers(),
                               timeout=self._timeout)
            return resp.json()
        req  = urllib.request.Request(
            url, data=payload, headers=self._headers(), method="POST")
        raw  = urllib.request.urlopen(req, timeout=self._timeout).read()
        return json.loads(raw)

    def _get(self, path: str) -> dict:
        url = f"{self._base}{path}"
        if _HTTPX:
            resp = _httpx.get(url, headers=self._headers(),
                              timeout=self._timeout)
            return resp.json()
        req = urllib.request.Request(url, headers=self._headers())
        raw = urllib.request.urlopen(req, timeout=self._timeout).read()
        return json.loads(raw)

    def agent_card(self) -> dict:
        """Fetch the remote agent's capability card."""
        return self._get("/.well-known/agent.json")

    def send_task(self, message: str,
                  session_id: str = "",
                  poll_interval: float = 1.5) -> str:
        """
        Send a task to the remote agent, poll until complete, return result.
        Raises RuntimeError on task failure.
        """
        body = {
            "message": {"role": "user",
                        "parts": [{"text": message}]},
            "sessionId": session_id or secrets.token_hex(4),
        }
        resp    = self._post("/a2a/tasks/send", body)
        task_id = resp.get("id", "")
        if not task_id:
            raise RuntimeError(f"A2A send_task: no task ID in response: {resp}")

        # Poll for completion
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            state = self._get(f"/a2a/tasks/{task_id}")
            status = state.get("status", {}).get("state", "")
            if status == "completed":
                history = state.get("history", [])
                for msg in reversed(history):
                    if msg.get("role") == "agent":
                        parts = msg.get("parts", [])
                        return " ".join(p.get("text", "") for p in parts)
                return "[A2A: task completed but no agent reply in history]"
            if status in ("failed", "cancelled"):
                err = state.get("error") or status
                raise RuntimeError(f"A2A task {task_id} {status}: {err}")
            time.sleep(poll_interval)

        raise TimeoutError(f"A2A task {task_id} timed out after {self._timeout}s")

    def cancel(self, task_id: str) -> bool:
        try:
            resp = self._post(f"/a2a/tasks/{task_id}/cancel", {})
            # Accept both AAIF single-l 'canceled' and common 'cancelled'
            state = resp.get("status", {}).get("state", "")
            return state in ("canceled", "cancelled")
        except Exception:
            return False


# Module-level A2A server singleton (initialised in cmd_up)
_a2a_server: A2AServer | None = None


# ══════════════════════════════════════════════════════════════════════════════

# A2A PEER REGISTRY + SOCIAL LAYER
# ══════════════════════════════════════════════════════════════════════════════
# The AgentCard and A2AServer in  give this Essence node the building blocks
# for an inter-agent ecosystem.  This section wires them into:
#
#   A2APeerRegistry  — maintains a persistent list of known peer agents.
#                      Supports static ENV config, manual registration, and
#                      mDNS LAN auto-discovery.  Peers are stored in
#                      workspace/a2a_peers.json.
#
#   A2APeerDiscovery — background thread that:
#                      1. Reads Essence_A2A_PEERS env var (csv of base URLs)
#                      2. Probes each URL's /.well-known/agent.json for a valid
#                         AgentCard and registers the peer automatically.
#                      3. Optionally broadcasts this node via mDNS/zeroconf
#                         (requires `zeroconf` package; degrades gracefully).
#
#   A2ANotificationBus — push task-completion events to interested peers.
#                        After A2AServer.update_task() transitions to
#                        "completed", the bus POSTs a lightweight notification
#                        to any peer that submitted the original task.
#
# CLI: `essence peers`  — list all known peers and their capabilities.

class A2APeerRegistry:
    """
    Persistent registry of known A2A peer agents.

    Each entry is the remote agent's AgentCard dict, augmented with:
      "base_url"     — the peer's root URL
      "last_seen"    — Unix timestamp of last successful probe
      "reachable"    — bool (set by most-recent probe)

    Storage: workspace/a2a_peers.json  (human-readable, hand-editable)
    """

    def __init__(self, workspace: Path) -> None:
        self._path  = workspace / "a2a_peers.json"
        self._peers: dict[str, dict] = {}   # keyed by base_url
        self._lock  = threading.Lock()
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._peers = data
            except Exception:
                self._peers = {}

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._peers, indent=2), encoding="utf-8")
        except Exception:
            pass

    def register(self, base_url: str, card: dict) -> None:
        """Add or update a peer entry."""
        url = base_url.rstrip("/")
        with self._lock:
            self._peers[url] = {**card,
                                 "base_url":  url,
                                 "last_seen": time.time(),
                                 "reachable": True}
            self._save()

    def mark_unreachable(self, base_url: str) -> None:
        url = base_url.rstrip("/")
        with self._lock:
            if url in self._peers:
                self._peers[url]["reachable"] = False
                self._save()

    def remove(self, base_url: str) -> None:
        url = base_url.rstrip("/")
        with self._lock:
            self._peers.pop(url, None)
            self._save()

    def all_peers(self) -> list[dict]:
        with self._lock:
            return list(self._peers.values())

    def reachable_peers(self) -> list[dict]:
        with self._lock:
            return [p for p in self._peers.values() if p.get("reachable")]

    def get(self, base_url: str) -> dict | None:
        return self._peers.get(base_url.rstrip("/"))

    def summary(self) -> str:
        peers = self.all_peers()
        if not peers:
            return "no A2A peers registered"
        lines = []
        for p in peers:
            mark  = "✓" if p.get("reachable") else "✗"
            name  = p.get("name", "unknown")
            ver   = p.get("version", "?")
            url   = p.get("base_url", "")
            skills = ", ".join(s.get("id", "") for s in p.get("skills", []))
            lines.append(f"  {mark} {name} v{ver}  {url}  [{skills}]")
        return "\n".join(lines)


class A2APeerDiscovery:
    """
    Background thread that probes and registers A2A peers.

    Sources (in priority order):
      1. Essence_A2A_PEERS env var — comma-separated list of base URLs
      2. mDNS / Zeroconf LAN scan — discovers _essence._tcp.local. services
         (requires `pip install zeroconf`; silently skipped otherwise)
      3. Manual registration via registry.register()

    Call start() to begin background probing.
    Call stop() to shut down cleanly.
    """

    _PROBE_INTERVAL = 60   # seconds between full re-probe cycles
    _MDNS_TYPE      = "_essence._tcp.local."

    def __init__(self, registry: A2APeerRegistry,
                 this_node_url: str = "") -> None:
        self._registry  = registry
        self._this_url  = this_node_url.rstrip("/")
        self._stop_evt  = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Per-peer rate limiting: don't hammer unreachable peers ─────────────────
    _PROBE_COOLDOWN   = 120   # seconds before re-probing a peer that was unreachable
    _PROBE_MAX_BURST  = 5     # max probes per _PROBE_INTERVAL cycle
    _probe_timestamps: dict[str, float] = {}  # class-level shared dict
    _probe_lock = threading.Lock()

    def _should_probe(self, base_url: str) -> bool:
        """Return True if sufficient time has passed since the last probe."""
        url = base_url.rstrip("/")
        with self._probe_lock:
            last = self._probe_timestamps.get(url, 0.0)
            peer = self._registry.get(url)
            # Use longer cooldown for peers already known to be unreachable
            cooldown = (self._PROBE_COOLDOWN * 3
                        if (peer and not peer.get("reachable", True))
                        else self._PROBE_COOLDOWN)
            return (time.time() - last) >= cooldown

    def _record_probe(self, base_url: str) -> None:
        """Record that a probe was just attempted."""
        with self._probe_lock:
            self._probe_timestamps[base_url.rstrip("/")] = time.time()

    def _probe_url(self, base_url: str) -> bool:
        """Fetch /.well-known/agent.json and register if valid. Returns success."""
        self._record_probe(base_url)
        url = f"{base_url.rstrip('/')}/.well-known/agent.json"
        try:
            req  = urllib.request.Request(
                url, headers={"User-Agent": f"Essence/{Essence_VERSION} A2ADiscovery"})
            raw  = urllib.request.urlopen(req, timeout=5).read()
            card = json.loads(raw)
            if not isinstance(card, dict) or "name" not in card:
                log.debug("a2a_probe_invalid_card",
                          extra={"url": base_url[:60]})
                self._registry.mark_unreachable(base_url)
                return False
            self._registry.register(base_url, card)
            log.debug("a2a_probe_ok",
                      extra={"url": base_url[:60], "agent": card.get("name","?")})
            return True
        except Exception as e:
            log.debug("a2a_probe_failed",
                      extra={"url": base_url[:60], "error": str(e)[:60]})
            self._registry.mark_unreachable(base_url)
            return False

    def _probe_env_peers(self) -> None:
        raw = os.environ.get("Essence_A2A_PEERS", "")
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        if not urls:
            return
        log.debug("a2a_probe_env_peers", extra={"count": len(urls)})
        for url in urls:
            if url != self._this_url:
                try:
                    self._probe_url(url)
                except Exception as e:
                    log.warning("a2a_probe_env_error",
                                extra={"url": url[:60], "error": str(e)[:80]})

    def _probe_existing_peers(self) -> None:
        """Re-probe already-known peers to refresh reachability (rate-limited)."""
        probed = 0
        for peer in self._registry.all_peers():
            url = peer.get("base_url", "")
            if not url or url == self._this_url:
                continue
            if probed >= self._PROBE_MAX_BURST:
                break   # don't hammer all peers in one cycle
            if not self._should_probe(url):
                continue  # within cooldown window
            self._probe_url(url)
            probed += 1

    def _mdns_scan(self) -> None:
        """Scan LAN via mDNS for _essence._tcp.local. services."""
        try:
            from zeroconf import ServiceBrowser, Zeroconf  # type: ignore

            found_urls: list[str] = []
            # name → url mapping so remove/update can deregister stale entries
            _url_by_name: dict[str, str] = {}

            class _Listener:
                def add_service(self_, zc: Any, stype: str, name: str) -> None:
                    info = zc.get_service_info(stype, name)
                    if info:
                        host = info.parsed_addresses()[0] if info.parsed_addresses() else ""
                        port = info.port
                        if host and port:
                            url = f"http://{host}:{port}"
                            _url_by_name[name] = url
                            if url not in found_urls:
                                found_urls.append(url)

                def remove_service(self_, zc: Any, stype: str, name: str) -> None:
                    url = _url_by_name.pop(name, None)
                    if url and url in found_urls:
                        found_urls.remove(url)

                def update_service(self_, zc: Any, stype: str, name: str) -> None:
                    # Re-resolve and overwrite the existing entry (#14)
                    info = zc.get_service_info(stype, name)
                    old_url = _url_by_name.get(name)
                    if old_url and old_url in found_urls:
                        found_urls.remove(old_url)
                    if info:
                        host = info.parsed_addresses()[0] if info.parsed_addresses() else ""
                        port = info.port
                        if host and port:
                            new_url = f"http://{host}:{port}"
                            _url_by_name[name] = new_url
                            if new_url not in found_urls:
                                found_urls.append(new_url)

            zc = Zeroconf()
            _mdns_done = threading.Event()
            try:
                ServiceBrowser(zc, self._MDNS_TYPE, _Listener())
                _mdns_done.wait(timeout=3)   # non-blocking: unblocks on stop_evt too
            finally:
                zc.close()

            for url in found_urls:
                if url != self._this_url:
                    self._probe_url(url)
        except ImportError:
            pass   # zeroconf not installed — skip silently
        except Exception as e:
            log.debug("a2a_mdns_scan_error", extra={"error": str(e)[:80]})

    def _register_self_mdns(self, port: int) -> None:
        """Advertise this node via mDNS so LAN peers can discover it."""
        try:
            from zeroconf import ServiceInfo, Zeroconf  # type: ignore
            import socket as _sock
            local_ip = _sock.gethostbyname(_sock.gethostname())
            info = ServiceInfo(
                self._MDNS_TYPE,
                f"essence-{secrets.token_hex(4)}.{self._MDNS_TYPE}",
                addresses=[_sock.inet_aton(local_ip)],
                port=port,
                properties={"version": Essence_VERSION},
            )
            zc = Zeroconf()
            zc.register_service(info)
            log.info("a2a_mdns_registered",
                     extra={"ip": local_ip, "port": port})
        except ImportError:
            pass
        except Exception as e:
            log.debug("a2a_mdns_register_error", extra={"error": str(e)[:80]})

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._probe_env_peers()
                self._probe_existing_peers()
                self._mdns_scan()
            except Exception as e:
                log.debug("a2a_discovery_cycle_error", extra={"error": str(e)[:80]})
            self._stop_evt.wait(self._PROBE_INTERVAL)

    def start(self, server_port: int = 7860) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._register_self_mdns(server_port)
        self._thread = threading.Thread(
            target=self._run, name="a2a-discovery", daemon=True)
        self._thread.start()
        log.info("a2a_discovery_started")

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=5)


class A2ANotificationBus:
    """
    Pushes lightweight task-completion events to peer agents.

    When A2AServer.update_task() transitions a task to "completed" or "failed",
    call notify_peers() to POST a compact notification to any peer whose
    base_url is in the registry.  The receiving peer can use this to:
      • Unblock a waiting A2AClient.send_task() poll faster
      • Trigger a cascading downstream task
      • Log cross-agent task history

    Notification payload (POST to /a2a/notifications on the peer):
      { "event": "task_updated",
        "from":  "<this_node_url>",
        "task":  { ...A2ATask.to_dict() } }
    """

    def __init__(self, registry: A2APeerRegistry,
                 this_node_url: str = "") -> None:
        self._registry = registry
        self._this_url = this_node_url.rstrip("/")

    def notify_peers(self, task: A2ATask, token: str = "") -> None:
        """
        POST task-completion notification to all reachable peers.
        Runs in a fire-and-forget daemon thread; never blocks the caller.
        """
        payload = json.dumps({
            "event": "task_updated",
            "from":  self._this_url,
            "task":  task.to_dict(),
        }).encode()
        for peer in self._registry.reachable_peers():
            url = f"{peer['base_url']}/a2a/notifications"
            threading.Thread(
                target=self._post_one,
                args=(url, payload, token),
                daemon=True,
            ).start()

    @staticmethod
    def _post_one(url: str, payload: bytes, token: str) -> None:
        headers: dict = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            urllib.request.urlopen(
                urllib.request.Request(url, data=payload,
                                       headers=headers, method="POST"),
                timeout=8)
        except Exception:
            pass   # best-effort; peer may be offline


class SmartPeerSelector:
    """
    Smart multi-agent peer selection service.

    Ranks reachable peers by a composite score combining:
      - capability match (does the peer expose the required skill?)
      - latency score (derived from probe round-trip times, EMA-smoothed)
      - recency (time since last successful probe)
      - load estimate (optional: peers may advertise load in their AgentCard)

    Usage:
        selector = SmartPeerSelector(registry)
        best = selector.select(required_skills=["web_browse", "code_exec"])
        # best → list[dict] sorted best-first
    """

    _LATENCY_ALPHA = 0.3   # EMA smoothing factor for latency scores
    _MAX_AGE       = 300   # seconds — peers older than this are penalised

    def __init__(self, registry: "A2APeerRegistry") -> None:
        self._registry = registry
        self._latency_ema: dict[str, float] = {}   # url → EMA latency (s)
        self._lock = threading.Lock()

    # ── Latency tracking ─────────────────────────────────────────────────────

    def record_latency(self, base_url: str, latency_s: float) -> None:
        """Update the EMA latency for a peer after a task round-trip."""
        url = base_url.rstrip("/")
        with self._lock:
            prev = self._latency_ema.get(url, latency_s)
            self._latency_ema[url] = (
                self._LATENCY_ALPHA * latency_s
                + (1 - self._LATENCY_ALPHA) * prev
            )

    def _latency_score(self, base_url: str) -> float:
        """Return a [0, 1] score; higher is faster (lower latency is better)."""
        url = base_url.rstrip("/")
        with self._lock:
            ema = self._latency_ema.get(url)
        if ema is None:
            return 0.5   # unknown — neutral score
        # Normalise: 0s → 1.0, ≥5s → 0.0
        return max(0.0, 1.0 - ema / 5.0)

    # ── Capability matching ───────────────────────────────────────────────────

    @staticmethod
    def _peer_skill_ids(peer: dict) -> set[str]:
        """Extract the set of skill IDs advertised in a peer's AgentCard."""
        skills = peer.get("skills", [])
        ids: set[str] = set()
        for s in skills:
            if isinstance(s, dict):
                sid = s.get("id") or s.get("name", "")
                if sid:
                    ids.add(str(sid).lower())
        return ids

    def _capability_score(self, peer: dict, required_skills: list[str]) -> float:
        """
        Return the fraction of required skills covered by this peer [0, 1].
        0.5 base score when no requirements are specified.
        """
        if not required_skills:
            return 0.5
        peer_skills = self._peer_skill_ids(peer)
        hits = sum(1 for s in required_skills if s.lower() in peer_skills)
        return hits / len(required_skills)

    # ── Composite scoring ─────────────────────────────────────────────────────

    def score_peer(self, peer: dict,
                   required_skills: list[str] | None = None) -> float:
        """
        Composite score for a single peer (higher is better).

        Weights:
          capability  40 %
          latency     35 %
          recency     25 %
        """
        url = peer.get("base_url", "")
        cap     = self._capability_score(peer, required_skills or [])
        lat     = self._latency_score(url)
        age_s   = time.time() - peer.get("last_seen", 0)
        recency = max(0.0, 1.0 - age_s / self._MAX_AGE)
        load_pen = peer.get("load", 0.0)   # 0..1 advertised load → penalty
        return 0.40 * cap + 0.35 * lat + 0.25 * recency - 0.15 * load_pen

    # ── Selection ─────────────────────────────────────────────────────────────

    def select(self,
               required_skills: list[str] | None = None,
               limit: int = 5,
               exclude_urls: list[str] | None = None) -> list[dict]:
        """
        Return the top-`limit` reachable peers sorted by composite score.

        Args:
            required_skills: skill IDs the chosen peer must cover.
            limit:           max peers to return.
            exclude_urls:    URLs to skip (e.g. already-tried peers).

        Returns list of peer dicts (AgentCard + base_url) with an added
        "__score" key so callers can inspect the ranking.
        """
        exclude = set(u.rstrip("/") for u in (exclude_urls or []))
        candidates = [
            p for p in self._registry.reachable_peers()
            if p.get("base_url", "").rstrip("/") not in exclude
        ]
        scored = []
        for p in candidates:
            s = self.score_peer(p, required_skills)
            scored.append({**p, "__score": round(s, 4)})
        scored.sort(key=lambda x: x["__score"], reverse=True)
        return scored[:limit]

    def best(self,
             required_skills: list[str] | None = None,
             exclude_urls: list[str] | None = None) -> dict | None:
        """Convenience: return the single best peer or None."""
        ranked = self.select(required_skills, limit=1, exclude_urls=exclude_urls)
        return ranked[0] if ranked else None

    # ── Summary ───────────────────────────────────────────────────────────────

    def ranked_summary(self,
                       required_skills: list[str] | None = None) -> str:
        ranked = self.select(required_skills, limit=10)
        if not ranked:
            return "no reachable peers"
        lines = []
        for i, p in enumerate(ranked, 1):
            lines.append(
                f"  {i}. {p.get('name','?')} @ {p.get('base_url','?')} "
                f"[score={p['__score']:.3f}]"
            )
        return "\n".join(lines)


# Module-level peer-layer singletons (initialised in cmd_up)
_a2a_peer_registry:  A2APeerRegistry  | None = None
_a2a_peer_discovery: A2APeerDiscovery | None = None
_a2a_notify_bus:     A2ANotificationBus | None = None
_a2a_smart_selector: SmartPeerSelector | None = None


def _init_a2a_social(workspace: Path, public_url: str, port: int = 7860) -> None:
    """
    Initialise the full A2A social layer.  Called once from cmd_up().
    Sets module-level singletons so they are accessible from FastAPI routes.
    """
    global _a2a_peer_registry, _a2a_peer_discovery, _a2a_notify_bus, _a2a_smart_selector
    _a2a_peer_registry  = A2APeerRegistry(workspace)
    _a2a_peer_discovery = A2APeerDiscovery(_a2a_peer_registry, public_url)
    _a2a_notify_bus     = A2ANotificationBus(_a2a_peer_registry, public_url)
    _a2a_smart_selector = SmartPeerSelector(_a2a_peer_registry)
    _a2a_peer_discovery.start(server_port=port)
    log.info("a2a_social_layer_ready",
             extra={"peers_known": len(_a2a_peer_registry.all_peers())})


def _tool_build_skill(description: str, workspace: Path,
                      provider: Any, model: str) -> str:
    """
    Auto-skill builder: instruct the agent to scaffold a new SKILL.md.
    The agent writes the skill content; it is saved to workspace/skills/<slug>/SKILL.md.
    On first run the skill is sandboxed via SkillRunner to its declared capabilities.
    """
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "-", description.lower().strip())[:40].strip("-")
    skill_dir = workspace / "skills" / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    if skill_path.exists():
        return f"[build_skill] Skill '{slug}' already exists at {skill_path}"

    prompt = (
        f"Write a complete SKILL.md for a Essence skill that does the following:\n\n"
        f"{description}\n\n"
        "Format:\n"
        "---\n"
        "name: <slug>\n"
        "description: <one line>\n"
        "tools: [shell, read_file, write_file, python_exec, web_search]\n"
        "trigger: manual\n"
        "---\n\n"
        "# <Skill Title>\n\n"
        "<Step-by-step instructions the agent follows when this skill activates.>\n\n"
        "Output ONLY the SKILL.md content. No prose, no markdown fences."
    )
    try:
        out = ""
        for tok in provider.complete(
            [{"role": "user", "content": prompt}],
            model=model, stream=False, thinking=False,
        ):
            out += tok
        content = out.strip()
        skill_path.write_text(content, encoding="utf-8")
        return (f"[build_skill] Skill '{slug}' created at {skill_path}\n"
                f"Preview:\n{content[:400]}")
    except Exception as e:
        return f"[build_skill error: {e}]"


def _tool_skill_write(skill_name: str, description: str, skill_md: str,
                      tool_py: str, requirements: str,
                      workspace: Path, agent: Any) -> str:
    """
    Self-authoring skill tool: write + validate + hot-reload a new skill.

    Flow:
      1. Sanitise slug and create workspace/skills/<slug>/
      2. Write SKILL.md (required), tool.py (optional), requirements.txt (optional)
      3. Install requirements via pip --user in a subprocess
      4. Syntax-check tool.py in an isolated subprocess
      5. Hot-reload the global skills index and register tool.py handler
      6. Return a status summary

    Called by the agent when it detects a capability gap and decides a
    persistent reusable skill would close it.  All writes are inside the
    workspace; the subprocess pip install is the only external side-effect.
    """
    import re as _re, subprocess as _sp
    if not skill_name or not skill_md:
        return "[skill_write] skill_name and skill_md are required"

    slug = _re.sub(r"[^a-z0-9]+", "-", skill_name.lower().strip())[:40].strip("-")
    if not slug:
        return "[skill_write] skill_name produced an empty slug"

    skill_dir = workspace / "skills" / slug
    skill_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write SKILL.md
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    written = ["SKILL.md"]

    # 2. Write optional tool.py
    if tool_py and tool_py.strip():
        (skill_dir / "tool.py").write_text(tool_py, encoding="utf-8")
        written.append("tool.py")

    # 3. Write optional requirements.txt
    if requirements and requirements.strip():
        (skill_dir / "requirements.txt").write_text(requirements, encoding="utf-8")
        written.append("requirements.txt")

    msgs: list[str] = [f"[skill_write] Created '{slug}': {', '.join(written)}"]

    # 4. Install requirements
    req_path = skill_dir / "requirements.txt"
    if req_path.exists():
        try:
            r = _sp.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_path),
                 "--user", "--quiet", "--break-system-packages"],
                capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                msgs.append("  requirements installed")
            else:
                msgs.append(f"  pip warning: {r.stderr.strip()[:200]}")
        except Exception as e:
            msgs.append(f"  pip error: {e}")

    # 5. Syntax-check tool.py
    tool_path = skill_dir / "tool.py"
    if tool_path.exists():
        try:
            r = _sp.run(
                [sys.executable, "-c",
                 f"import ast; ast.parse(open({str(tool_path)!r}).read())"],
                capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                msgs.append("  tool.py syntax OK")
            else:
                msgs.append(f"  tool.py syntax error: {r.stderr.strip()[:200]}")
        except Exception as e:
            msgs.append(f"  tool.py check error: {e}")

    # 6. Hot-reload skills index into the live agent
    try:
        if hasattr(agent, "skills"):
            agent.skills = load_skills_index(workspace)
            msgs.append(f"  hot-reloaded skills index ({len(agent.skills)} skills)")
            if hasattr(agent, "reload_identity_files"):
                agent.reload_identity_files()
    except Exception as e:
        msgs.append(f"  reload warning: {e}")

    return "\n".join(msgs)


# ══════════════════════════════════════════════════════════════════════════════
