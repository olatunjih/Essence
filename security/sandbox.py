"""OS sandbox +  container sandbox: blocklist, PII regex, ProcessSandbox."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# SECURITY SANDBOX
# ══════════════════════════════════════════════════════════════════════════════
# Essence's #1 security vulnerability (per Cisco AI Security 2025 report):
# no deterministic circuit-breaker on shell tool calls. Malicious skill hub
# skills performed data exfiltration without user awareness.
#
# CriticGate category: "Permission Violation" — we pre-empt it here.
# Two layers: (1) pattern blocklist, (2) workspace path guard.
# Prompt injection detection in tool inputs (prevents "ignore previous").

_BLOCKLIST = [
    r"rm\s+-[rRf]+\s+/",        r"mkfs\.",          r"dd\s+if=",
    r"shred\s+",                  r":\(\)\{.*?\}\s*;", # fork bomb
    r"cat\s+~?/?\.ssh/",         r"cat\s+.*\.pem\b",
    r"env\s*\|",                  r"printenv\b",
    r"curl\s+[^|]+\|\s*(?:ba)?sh", r"wget\s+[^|]+\|\s*(?:ba)?sh",
    r"nc\s+-[el]",                r"python\d?\s+-c\s+.*socket",
    r"sudo\s+",                   r"su\s+-\s",
    r"chmod\s+[0-7]*7[0-7]*",    r"chown\s+root",
    r"base64\s+--decode",         r"eval\s+\$\(",
    r"export\s+.*(?:KEY|TOKEN|SECRET|PASSWORD)",
    # /tmp staging: block executing scripts staged in /tmp
    r"(?:ba)?sh\s+/tmp/",         r"python\d?\s+/tmp/",
    r"perl\s+/tmp/",              r"ruby\s+/tmp/",
    r"curl\s+-[oO]\s+/tmp/",     r"wget\s+-[oO]\s+/tmp/",
]
_BLOCK_RE    = [re.compile(p, re.I | re.S) for p in _BLOCKLIST]

# Prompt injection phrases in tool args
_INJECTION_RE = re.compile(
    r"(ignore\s+(all\s+)?previous|disregard\s+instructions|"
    r"new\s+instructions|you\s+are\s+now|forget\s+(all\s+)?previous|"
    r"\[INST\]|<\|im_start\|>|<\|system\|>)",
    re.I)

# PII / secrets patterns for SemanticGuard
_PII_RE = re.compile(
    r"(\b\d{3}-\d{2}-\d{4}\b"           # SSN
    r"|\b(?:sk|pk)-[A-Za-z0-9]{20,}\b"  # API keys
    r"|AKIA[0-9A-Z]{16}"                 # AWS keys
    r"|-----BEGIN (?:RSA |EC )?PRIVATE KEY)",
    re.I)


_SECRET_MASK_RE = re.compile(
    r'((?:sk|pk)-[A-Za-z0-9]{8})[A-Za-z0-9]+|'
    r'(AKIA[A-Z0-9]{4})[A-Z0-9]+|'
    r'(ghp_[A-Za-z0-9]{4})[A-Za-z0-9]+|'
    r'(Bearer\s+\S{8})\S*|'
    r'(bot\d+:)[A-Za-z0-9_-]+',
    re.I
)

def _mask_secrets(text: str) -> str:
    """Replace recognisable secrets with masked versions for safe logging."""
    return _SECRET_MASK_RE.sub(lambda m: (m.group(0)[:len(m.group(0))//2] + "***"), text)


def semantic_guard(content: str) -> str | None:
    """
    SemanticGuard: scan tool results before injecting into LLM context.
    Returns sanitized summary string if suspicious content is found,
    or None if content is clean (caller uses original in that case).

    Catches: embedded prompt injection, PII (SSN, API keys), and
    common jailbreak delimiters that could hijack the agent context window.
    """
    if _INJECTION_RE.search(content):
        return ("[SEMANTIC_GUARD: prompt injection pattern detected in tool "
                "result — content replaced for safety]")
    if _PII_RE.search(content):
        return ("[SEMANTIC_GUARD: sensitive data (PII/credentials) detected in "
                "tool result — content redacted]")
    return None


def sandbox_check(cmd: str, workspace: Path,
                  allow_outside: bool = False) -> str | None:
    """Return error string if blocked, None if safe."""
    # Prompt injection in the command itself
    if _INJECTION_RE.search(cmd):
        return "[BLOCKED: prompt injection attempt detected in command]"
    # Dangerous pattern blocklist
    for pat in _BLOCK_RE:
        if pat.search(cmd):
            return f"[BLOCKED: dangerous pattern '{pat.pattern[:40]}']"
    # Workspace path guard
    if not allow_outside:
        for token in re.findall(r'[/~][^\s"\']+', cmd):
            resolved = token.replace("~", str(Path.home()))
            if resolved.startswith("/") and \
               not resolved.startswith(str(workspace)) and \
               not any(resolved.startswith(safe)
                       for safe in ("/tmp", *(["/proc/cpuinfo", "/proc/meminfo", "/proc/loadavg", "/proc/uptime"] if platform.system() == "Linux" else []), "/sys/class",
                                    "/usr/bin", "/usr/local/bin")):
                return f"[BLOCKED: path outside workspace — {token}]"
    return None


# ══════════════════════════════════════════════════════════════════════════════

# CONTAINER SANDBOX
# ══════════════════════════════════════════════════════════════════════════════
# When Essence_CONTAINER=1 is set, shell tool calls are routed through a
# fresh ephemeral container (Docker / Podman / nerdctl) instead of running
# directly in the host process.
#
# This provides OS-level enforcement that application-level blocklists cannot:
#   • Filesystem isolation  — container sees only a bind-mounted /workspace copy
#   • Network isolation     — --network=none by default; opt-in with Essence_CONTAINER_NET=1
#   • PID/user namespace    — runs as UID 65534 (nobody) inside the container
#   • CPU/memory limits     — 1 CPU, 512 MB RAM by default (Essence_CONTAINER_MEM/CPU)
#   • Automatic cleanup     — --rm ensures the container never persists
#
# Container-sandbox design: when the container runtime is available, the
# application-level sandbox_check() is still applied first as a fast pre-filter,
# then the surviving command is handed to the container for OS-enforced execution.
#
# Env vars:
#   Essence_CONTAINER=1                 Enable container routing (on by default)
#   Essence_CONTAINER_RUNTIME=docker    docker | podman | nerdctl  (auto-detected)
#   Essence_CONTAINER_IMAGE=python:3.12 Base image  (default: python:3.12-slim)
#   Essence_CONTAINER_NET=0             0=no network (default), 1=bridge
#   Essence_CONTAINER_MEM=512m          Memory limit (default: 512m)
#   Essence_CONTAINER_CPU=1             CPU count limit (default: 1)
#   Essence_CONTAINER_TIMEOUT=30        Hard wall-clock timeout in seconds

_CONTAINER_ENABLED = os.environ.get("Essence_CONTAINER", "1") == "1"
if os.environ.get("Essence_CONTAINER") == "0":
    log.warning("container_sandbox_disabled",
                extra={"detail": "Essence_CONTAINER=0 set — shell tools running without OS-level isolation!"})
_CONTAINER_RUNTIME = os.environ.get("Essence_CONTAINER_RUNTIME", "")   # auto-detected if blank
_CONTAINER_IMAGE   = os.environ.get("Essence_CONTAINER_IMAGE", "python:3.12-slim")
_CONTAINER_NET     = "bridge" if os.environ.get("Essence_CONTAINER_NET", "0") == "1" else "none"
_CONTAINER_MEM     = os.environ.get("Essence_CONTAINER_MEM", "512m")
_CONTAINER_CPU     = os.environ.get("Essence_CONTAINER_CPU", "1")
_CONTAINER_TIMEOUT = int(os.environ.get("Essence_CONTAINER_TIMEOUT", "30"))


def _detect_container_runtime() -> str | None:
    """Return the first available container runtime binary, or None."""
    for rt in (_CONTAINER_RUNTIME,) if _CONTAINER_RUNTIME else ("docker", "podman", "nerdctl"):
        if rt and shutil.which(rt):
            return rt
    return None


class _EphemeralContainerSandbox:
    """
    OS-level container sandbox for shell tool execution (ephemeral one-shot).

    Usage (called automatically by _tool_shell when Essence_CONTAINER=1):
        cs = _EphemeralContainerSandbox(workspace)
        result = cs.run("ls -la /workspace")

    The workspace directory is bind-mounted read-only at /workspace inside
    the container; a writable /tmp is provided via tmpfs.  The container is
    always removed after execution (--rm).

    Direct usage for testing:
        cs = _EphemeralContainerSandbox(Path("/my/workspace"))
        ok, output = cs.available(), cs.run("echo hello")
    """

    def __init__(self, workspace: Path) -> None:
        self._ws      = workspace
        self._runtime = _detect_container_runtime()

    def available(self) -> bool:
        """True when a container runtime is found and Essence_CONTAINER=1."""
        return _CONTAINER_ENABLED and self._runtime is not None

    def run(self, cmd: str, timeout: int | None = None) -> str:
        """
        Execute cmd inside an ephemeral container.
        Returns combined stdout+stderr as a string.
        Raises RuntimeError if the runtime is not available.
        """
        if not self._runtime:
            raise RuntimeError(
                "_EphemeralContainerSandbox: no container runtime found "
                "(install docker/podman/nerdctl and set Essence_CONTAINER=1)")

        wall = timeout or _CONTAINER_TIMEOUT

        # Build the container command
        docker_cmd = [
            self._runtime, "run", "--rm",
            "--network", _CONTAINER_NET,
            "--memory", _CONTAINER_MEM,
            "--cpus",   _CONTAINER_CPU,
            "--user",   "65534:65534",        # nobody:nobody
            "--read-only",                     # immutable root fs
            "--tmpfs",  "/tmp:rw,noexec,size=64m",
            "--volume", f"{self._ws}:/workspace:ro",
            "--workdir", "/workspace",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            _CONTAINER_IMAGE,
            "/bin/sh", "-c", cmd,
        ]

        try:
            proc = subprocess.run(
                docker_cmd,
                capture_output=True, text=True,
                timeout=wall,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                out += f"\n[container exit code {proc.returncode}]"
            return out.strip()
        except subprocess.TimeoutExpired:
            return f"[CONTAINER TIMEOUT: command exceeded {wall}s wall time]"
        except FileNotFoundError:
            return f"[CONTAINER ERROR: runtime '{self._runtime}' not found in PATH]"
        except Exception as e:
            return f"[CONTAINER ERROR: {e}]"

    def status(self) -> str:
        """Human-readable availability string for `essence doctor`."""
        if not _CONTAINER_ENABLED:
            return "container sandbox disabled (set Essence_CONTAINER=1 to enable)"
        if not self._runtime:
            return "Essence_CONTAINER=1 but no runtime found (install docker/podman/nerdctl)"
        return f"container sandbox ready: {self._runtime} → {_CONTAINER_IMAGE}"


# Module-level singleton; initialised lazily on first tool-shell call.
_container_sandbox: _EphemeralContainerSandbox | None = None


def get_container_sandbox(workspace: Path) -> _EphemeralContainerSandbox:
    """Return the module-level ContainerSandbox singleton."""
    global _container_sandbox
    if _container_sandbox is None:
        _container_sandbox = _EphemeralContainerSandbox(workspace)
    return _container_sandbox


class ProcessSandbox:
    """last-resort, same-host sandbox tier used when the container
    runtime is unavailable. Runs a zero-arg callable on a worker
    thread with a hard wall-clock timeout; the worker thread itself sets
    OS resource limits (CPU/AS/FSIZE) via `resource.setrlimit` on POSIX
    so a runaway command can't exhaust host memory/CPU even though it
    isn't a true forked/isolated process.

    [Previously referenced by tools/registry.py._tool_shell and named in
    this module's docstring, but the class itself was never written —
    real missing implementation, not just a missing import.]"""

    @staticmethod
    def run(fn: "Callable[[], str]", timeout: float = 17.0) -> str:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(ProcessSandbox._run_with_limits, fn)
            try:
                return fut.result(timeout=timeout)
            except _cf.TimeoutError:
                raise RuntimeError(f"command exceeded {timeout:.0f}s timeout")

    @staticmethod
    def _run_with_limits(fn: "Callable[[], str]") -> str:
        try:
            import resource  # type: ignore  # POSIX only
            resource.setrlimit(resource.RLIMIT_AS,   (1024 * 1024 * 1024,) * 2)
            resource.setrlimit(resource.RLIMIT_CPU,  (30, 30))
            resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024,) * 2)
        except Exception:
            pass  # resource module unavailable (e.g. Windows) — best-effort only
        return fn()


# ══════════════════════════════════════════════════════════════════════════════
