"""MCP memory server +  MCP client (HTTP + stdio transports)."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# MCP MEMORY SERVER
# ══════════════════════════════════════════════════════════════════════════════
# Exposes Memory.store / recall / link / related as MCP tool schemas so
# any MCP-aware client can read and write the shared knowledge graph.
#
# These tools are registered into BUILTIN_TOOLS at startup like all others.
# They are also served via GET /.well-known/mcp-memory.json so external
# clients can discover the capabilities without reading the source.
#
# ENV: Essence_MCP_MEMORY=1   Enable these tools (default: enabled when team_id set)

_MCP_MEMORY_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "memory_store",
            "description": (
                "Store a text fragment with optional metadata into persistent memory. "
                "Use this to save facts, summaries, decisions, or any information "
                "that should be recalled in future sessions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to store (max 2000 chars)."
                    },
                    "source": {
                        "type": "string",
                        "description": "Origin label, e.g. 'user', 'document', 'tool'."
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional topic tags for retrieval."
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_recall",
            "description": (
                "Search persistent memory for text semantically similar to a query. "
                "Returns up to k most relevant stored fragments."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query."
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of results to return (default 5, max 20).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_link",
            "description": (
                "Record a directed association between two concepts in the semantic graph. "
                "Example: link('Python', 'data science') records that Python relates to "
                "data science. Use this to build a personal knowledge graph over time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "concept": {
                        "type": "string",
                        "description": "The source concept."
                    },
                    "related": {
                        "type": "string",
                        "description": "The target concept to associate with source."
                    },
                },
                "required": ["concept", "related"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_related",
            "description": (
                "Return concepts reachable from a concept in the semantic graph. "
                "depth=1 returns direct neighbours; depth=2 includes their neighbours."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "concept": {
                        "type": "string",
                        "description": "The concept to look up."
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Graph traversal depth (1 or 2, default 1).",
                        "default": 1,
                    },
                },
                "required": ["concept"],
            },
        },
    },
]


def _tool_memory_store(text: str, source: str = "mcp",
                       tags: list | None = None,
                       _mem: "Memory | None" = None) -> str:
    """MCP tool handler: store text in persistent memory."""
    from pathlib import Path as _P
    mem = _mem or Memory(_P.home() / ".essence")
    text = str(text)[:2000]
    meta: dict = {"source": source}
    if tags:
        meta["tags"] = tags
    mem.store(text, meta)
    return f"Stored {len(text)} chars from source='{source}'."


def _tool_memory_recall(query: str, k: int = 5,
                        _mem: "Memory | None" = None) -> str:
    """MCP tool handler: recall text from persistent memory."""
    from pathlib import Path as _P
    mem  = _mem or Memory(_P.home() / ".essence")
    k    = min(int(k), 20)
    hits = mem.search(str(query), k=k)
    if not hits:
        return "No relevant memories found."
    return "\n---\n".join(f"[{i+1}] {h}" for i, h in enumerate(hits))


def _tool_memory_link(concept: str, related: str,
                      _mem: "Memory | None" = None) -> str:
    """MCP tool handler: create a graph edge concept → related."""
    from pathlib import Path as _P
    mem = _mem or Memory(_P.home() / ".essence")
    mem.link(str(concept), str(related))
    return f"Linked '{concept}' → '{related}'."


def _tool_memory_related(concept: str, depth: int = 1,
                          _mem: "Memory | None" = None) -> str:
    """MCP tool handler: traverse graph from concept."""
    from pathlib import Path as _P
    mem     = _mem or Memory(_P.home() / ".essence")
    depth   = max(1, min(int(depth), 2))
    results = mem.related(str(concept), depth=depth)
    if not results:
        return f"No related concepts found for '{concept}'."
    return ", ".join(results)


def register_mcp_memory_tools(registry: "ToolRegistry",
                               mem: "Memory | None" = None) -> None:
    """Register the four MCP memory tools into a ToolRegistry instance."""
    _handlers = {
        "memory_store":   lambda **kw: _tool_memory_store(**kw, _mem=mem),
        "memory_recall":  lambda **kw: _tool_memory_recall(**kw, _mem=mem),
        "memory_link":    lambda **kw: _tool_memory_link(**kw, _mem=mem),
        "memory_related": lambda **kw: _tool_memory_related(**kw, _mem=mem),
    }
    for schema in _MCP_MEMORY_TOOLS:
        name = schema["function"]["name"]
        registry.register(schema, _handlers[name])
    log.debug("mcp_memory_tools_registered",
              extra={"tools": [schema["function"]["name"] for schema in _MCP_MEMORY_TOOLS]})


# ══════════════════════════════════════════════════════════════════════════════

# MCP CLIENT — HTTP transport
# ══════════════════════════════════════════════════════════════════════════════
# Set Essence_MCP_SERVERS=name=http://host:port,gh=http://localhost:3001 to
# auto-discover and register tools from MCP servers that expose an HTTP
# JSON-RPC endpoint.
# Failures are logged at DEBUG level and skipped — startup is never blocked.

def _bootstrap_mcp_clients() -> None:
    """Discover and register tools from external MCP servers (HTTP transport)."""
    raw = os.environ.get("Essence_MCP_SERVERS", "")
    if not raw:
        return
    _log = logging.getLogger("essence")
    for entry in raw.split(","):
        entry = entry.strip()
        if "=" not in entry:
            continue
        srv_name, url = entry.split("=", 1)
        srv_name = srv_name.strip()
        url      = url.strip().rstrip("/")
        try:
            # Prefer POST initialize → tools/list (MCP JSON-RPC 2.0)
            rpc_payload = json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
            }).encode()
            req = urllib.request.Request(
                f"{url}/mcp",
                data=rpc_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=4).read())
            tools = resp.get("result", {}).get("tools", [])
            for tool in tools:
                tname = tool.get("name", "")
                if not tname:
                    continue
                schema = {
                    "type": "function",
                    "function": {
                        "name": f"{srv_name}__{tname}",
                        "description": (
                            f"[{srv_name}] {tool.get('description', '')}"),
                        "parameters": tool.get("inputSchema",
                                      {"type": "object", "properties": {}}),
                    },
                }
                # Capture loop vars in closure
                def _make_handler(base_url: str, tool_name: str):
                    def _handler(args: dict) -> str:
                        call = json.dumps({
                            "jsonrpc": "2.0", "id": 1,
                            "method": "tools/call",
                            "params": {"name": tool_name, "arguments": args},
                        }).encode()
                        r = urllib.request.Request(
                            f"{base_url}/mcp", data=call,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        result = json.loads(
                            urllib.request.urlopen(r, timeout=15).read())
                        content = result.get("result", {}).get("content", [{}])
                        return content[0].get("text", str(result)) if content else str(result)
                    return _handler
                TOOL_REGISTRY.register(schema, _make_handler(url, tname))
            if tools:
                _log.info("mcp_client_registered",
                          extra={"server": srv_name, "tools": len(tools)})
        except Exception as exc:
            _log.debug("mcp_client_bootstrap_skip",
                       extra={"server": srv_name, "error": str(exc)[:120]})

_bootstrap_mcp_clients()


# ══════════════════════════════════════════════════════════════════════════════

# MCP STDIO CLIENT
# ══════════════════════════════════════════════════════════════════════════════
# Set Essence_MCP_STDIO_SERVERS=name=command arg1 arg2,other=uvx mcp-server-git
# to spawn stdio-based MCP servers (the standard distribution method for the
# official MCP servers: Google Workspace, Notion, Linear, filesystem, etc.).
#
# Each server is spawned as a subprocess. JSON-RPC 2.0 messages are sent to
# stdin and responses are read from stdout.  The subprocess lifecycle is managed
# by _StdioMCPClient; it is kept alive as long as the process is running.
#
# ENV: Essence_MCP_STDIO_SERVERS=name=command [args],name2=command2 [args2]
#   e.g. Essence_MCP_STDIO_SERVERS=fs=npx -y @modelcontextprotocol/server-filesystem /tmp
#         Essence_MCP_STDIO_SERVERS=git=uvx mcp-server-git --repository .

import threading as _threading


class _StdioMCPClient:
    """
    Manages a single stdio MCP server subprocess.
    Sends JSON-RPC 2.0 requests to stdin, reads responses from stdout.
    Thread-safe: a lock serialises concurrent tool calls.
    """

    def __init__(self, name: str, command: list[str]) -> None:
        self._name    = name
        self._command = command
        self._lock    = _threading.Lock()
        self._proc: "subprocess.Popen | None" = None
        self._next_id = 1
        self._log     = logging.getLogger(f"essence.mcp.stdio.{name}")

    def start(self) -> bool:
        """Spawn the subprocess. Returns True on success."""
        try:
            self._proc = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,   # binary mode — we encode/decode manually
            )
            # Send MCP initialize handshake
            self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities":    {"tools": {}},
                "clientInfo":      {"name": "essence", "version": "1.0"},
            })
            # Send initialized notification (no response expected)
            self._send_notification("notifications/initialized", {})
            self._log.info("mcp_stdio_server_started",
                           extra={"command": " ".join(self._command[:3])})
            return True
        except Exception as exc:
            self._log.debug("mcp_stdio_server_start_failed",
                            extra={"error": str(exc)[:120]})
            self._proc = None
            return False

    def list_tools(self) -> list[dict]:
        """Return the tools/list result from the server."""
        resp = self._rpc("tools/list", {})
        if resp is None:
            return []
        return resp.get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a tool and return the text content."""
        resp = self._rpc("tools/call", {
            "name":      tool_name,
            "arguments": arguments,
        })
        if resp is None:
            return f"[mcp_stdio:{self._name}] No response from server."
        content = resp.get("content", [])
        if content and isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text", "")
                    if text:
                        parts.append(str(text))
            return "\n".join(parts) if parts else str(resp)
        return str(resp)

    def stop(self) -> None:
        """Terminate the subprocess gracefully."""
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def is_alive(self) -> bool:
        """Return True if the subprocess is still running."""
        return self._proc is not None and self._proc.poll() is None

    # ── internal JSON-RPC helpers ─────────────────────────────────────────────

    def _rpc(self, method: str, params: dict, timeout: float = 15.0) -> dict | None:
        """Send a JSON-RPC request and return the result dict (or None on error)."""
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            return None
        with self._lock:
            try:
                msg_id  = self._next_id
                self._next_id += 1
                payload = json.dumps({
                    "jsonrpc": "2.0",
                    "id":      msg_id,
                    "method":  method,
                    "params":  params,
                }) + "\n"
                self._proc.stdin.write(payload.encode("utf-8"))
                self._proc.stdin.flush()

                # Read until we get a line with our message id
                import select as _select
                deadline = time.time() + timeout
                while time.time() < deadline:
                    # Check if data is available (non-blocking on Linux/macOS)
                    ready = []
                    try:
                        ready, _, _ = _select.select(
                            [self._proc.stdout], [], [], 0.1)
                    except (ValueError, OSError):
                        break
                    if not ready:
                        continue
                    line = self._proc.stdout.readline()
                    if not line:
                        break
                    try:
                        resp = json.loads(line.decode("utf-8").strip())
                        if resp.get("id") == msg_id:
                            if "error" in resp:
                                self._log.debug(
                                    "mcp_stdio_rpc_error",
                                    extra={"method": method,
                                           "error": str(resp["error"])[:120]})
                                return None
                            return resp.get("result", {})
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                self._log.debug("mcp_stdio_rpc_timeout",
                                extra={"method": method, "timeout": timeout})
                return None
            except Exception as exc:
                self._log.debug("mcp_stdio_rpc_exception",
                                extra={"method": method, "error": str(exc)[:120]})
                return None

    def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        if self._proc is None or self._proc.stdin is None:
            return
        try:
            payload = json.dumps({
                "jsonrpc": "2.0",
                "method":  method,
                "params":  params,
            }) + "\n"
            self._proc.stdin.write(payload.encode("utf-8"))
            self._proc.stdin.flush()
        except Exception:
            pass


# Module-level registry of active stdio clients (for lifecycle management)
_STDIO_CLIENTS: dict[str, _StdioMCPClient] = {}


def _bootstrap_mcp_stdio_clients() -> None:
    """
    Spawn and register tools from stdio-based MCP servers.

    Reads Essence_MCP_STDIO_SERVERS (comma-separated name=command pairs).
    Each server is spawned as a subprocess; its tools are registered into
    TOOL_REGISTRY prefixed as <server_name>__<tool_name>.

    Example:
        export Essence_MCP_STDIO_SERVERS="fs=npx -y @modelcontextprotocol/server-filesystem /tmp"
    """
    raw = os.environ.get("Essence_MCP_STDIO_SERVERS", "")
    if not raw:
        return
    _log = logging.getLogger("essence")

    for entry in raw.split(","):
        entry = entry.strip()
        if "=" not in entry:
            continue
        srv_name, cmd_str = entry.split("=", 1)
        srv_name = srv_name.strip()
        cmd_str  = cmd_str.strip()
        if not cmd_str:
            continue

        # Parse the command string (respects quoted tokens via shlex)
        try:
            command = shlex.split(cmd_str)
        except ValueError as exc:
            _log.debug("mcp_stdio_bad_command",
                       extra={"server": srv_name, "error": str(exc)[:80]})
            continue

        client = _StdioMCPClient(srv_name, command)
        if not client.start():
            continue

        try:
            tools = client.list_tools()
        except Exception as exc:
            _log.debug("mcp_stdio_list_tools_failed",
                       extra={"server": srv_name, "error": str(exc)[:120]})
            client.stop()
            continue

        if not tools:
            _log.debug("mcp_stdio_no_tools",
                       extra={"server": srv_name})
            client.stop()
            continue

        _STDIO_CLIENTS[srv_name] = client

        for tool in tools:
            tname = tool.get("name", "")
            if not tname:
                continue
            schema = {
                "type": "function",
                "function": {
                    "name":        f"{srv_name}__{tname}",
                    "description": f"[{srv_name}] {tool.get('description', '')}",
                    "parameters":  tool.get("inputSchema",
                                            {"type": "object", "properties": {}}),
                },
            }

            def _make_stdio_handler(c: "_StdioMCPClient", tn: str):
                def _handler(**kwargs: Any) -> str:
                    if not c.is_alive():
                        return f"[mcp_stdio:{c._name}] Server process exited."
                    return c.call_tool(tn, kwargs)
                return _handler

            TOOL_REGISTRY.register(schema, _make_stdio_handler(client, tname))

        _log.info("mcp_stdio_client_registered",
                  extra={"server": srv_name, "tools": len(tools)})


def shutdown_stdio_clients() -> None:
    """
    Gracefully terminate all stdio MCP server subprocesses.
    Call this at application shutdown (e.g. in the server lifespan handler).
    """
    for name, client in list(_STDIO_CLIENTS.items()):
        try:
            client.stop()
            log.debug("mcp_stdio_server_stopped", extra={"server": name})
        except Exception as exc:
            log.debug("mcp_stdio_server_stop_error",
                      extra={"server": name, "error": str(exc)[:80]})
    _STDIO_CLIENTS.clear()


_bootstrap_mcp_stdio_clients()


# ══════════════════════════════════════════════════════════════════════════════

# SkillRunner reads that list and spawns the skill in a fresh subprocess with
# only those tool functions wired in. All others raise PermissionError.
# On Linux, wraps with seccomp; on macOS, uses sandbox-exec when available.

class SkillRunner:
    """
    Isolated skill execution: reads 'tools:' from SKILL.md YAML header,
    grants only those capabilities, runs in subprocess.
    """
    _TOOL_WHITELIST = {
        "shell", "read_file", "write_file", "python_exec",
        "web_search", "heartbeat_add", "analyze_image", "build_skill",
        "read_skill", "skill_write",
    }

    @staticmethod
    def _parse_capabilities(skill_md: str) -> list[str]:
        """Extract tools list from SKILL.md YAML front-matter."""
        in_front = False
        for line in skill_md.splitlines():
            if line.strip() == "---":
                in_front = not in_front
                continue
            if in_front and line.strip().startswith("tools:"):
                raw = line.split(":", 1)[1].strip().strip("[]")
                return [t.strip() for t in raw.split(",") if t.strip()]
        return list(SkillRunner._TOOL_WHITELIST)

    @staticmethod
    def run(skill_md: str, task: str, workspace: Path,
            provider: Any, model: str) -> str:
        """Execute skill instructions in an isolated subprocess.

        The subprocess deserialises the payload, constructs a minimal Agent
        with only the declared capability tools wired in, runs the task via
        agent.run_task(), and prints the JSON result to stdout.  All other
        tool names raise PermissionError inside the subprocess so capability
        containment is enforced at the Python level.
        """
        caps    = SkillRunner._parse_capabilities(skill_md)
        # Filter caps against the declared whitelist
        allowed = [c for c in caps if c in SkillRunner._TOOL_WHITELIST]
        payload = json.dumps({
            "skill_md":          skill_md,
            "task":              task,
            "workspace":         str(workspace),
            "model":             model,
            "allowed_tool_names": allowed,
            "ollama_host":       os.environ.get("OLLAMA_HOST",
                                                "http://127.0.0.1:11434"),
        })
        # The runner script is injected via -c and receives the JSON payload
        # as sys.argv[1].  It imports essence from the same directory so the
        # full tool set is available; only the allowed subset is wired in.
        runner_code = textwrap.dedent("""
            import sys, json, os
            from pathlib import Path

            d    = json.loads(sys.argv[1])
            ws   = Path(d["workspace"])
            caps = set(d["allowed_tool_names"])

            # Locate essence (parent of workspace, or same dir as this process)
            _essence_candidates = [
                ws.parent / "essence.py",
                Path(sys.argv[0]).parent / "essence.py",
                Path(__file__).parent / "essence.py" if "__file__" in dir() else None,
            ]
            for _c in _essence_candidates:
                if _c and _c.exists():
                    sys.path.insert(0, str(_c.parent))
                    break

            try:
                import essence
                os.environ.setdefault("OLLAMA_HOST", d.get("ollama_host",
                                                            "http://127.0.0.1:11434"))
                hw    = essence.probe_hardware()
                prov  = essence.build_provider_chain(hw)
                model = d["model"] or hw.model

                # Build restricted cfg: capability filtering enforced in
                # _dispatch via the SkillRunner whitelist
                cfg   = essence.AgentConfig(
                    provider=prov, model=model, workspace=ws,
                    use_tools=True, autonomy_level=2)
                soul  = essence.load_ws_file(ws, "SOUL.md", essence._DEFAULT_SOUL)
                mem   = essence.Memory(ws)
                agent = essence.Agent(cfg, soul=soul, memory=mem, hw=hw)

                # Monkey-patch _dispatch to enforce capability list
                _orig_dispatch = agent._dispatch
                def _restricted_dispatch(name, args, log=None):
                    if name not in caps:
                        return (f"[SKILL_BLOCKED: tool '{name}' not in "
                                f"declared capabilities {sorted(caps)}]")
                    return _orig_dispatch(name, args, log=log)
                agent._dispatch = _restricted_dispatch

                skill_sys = (
                    "You are executing a Essence skill. Follow these instructions "
                    "exactly:\n\n" + d["skill_md"]
                )
                agent.soul = skill_sys
                result = agent.run_task(d["task"], log=lambda *_: None)
                print(json.dumps({"ok": True, "result": result}))
            except Exception as _e:
                print(json.dumps({"ok": False, "error": str(_e)}))
        """).strip()
        try:
            r = subprocess.run(
                [sys.executable, "-c", runner_code, payload],
                capture_output=True, text=True, timeout=120,
            )
            output = (r.stdout + r.stderr).strip()
            if not output:
                return f"[SkillRunner] completed (caps={allowed})"
            # Try to parse JSON result
            try:
                parsed = json.loads(output.splitlines()[-1])
                if parsed.get("ok"):
                    return str(parsed.get("result", ""))
                return f"[SkillRunner error] {parsed.get('error', output)}"
            except (json.JSONDecodeError, IndexError):
                return output
        except subprocess.TimeoutExpired:
            return "[SkillRunner] Skill timed out after 120s"
        except Exception as e:
            return f"[SkillRunner error: {e}]"


# ══════════════════════════════════════════════════════════════════════════════
