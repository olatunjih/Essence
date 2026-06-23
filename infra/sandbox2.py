""" — sandboxed code executor (subprocess tier)."""
from __future__ import annotations
import hashlib
import io
import multiprocessing as _mp
import sys
import textwrap
from pathlib import Path
from typing import Any


class SandboxedExecutor:
    """
     — in-process-tier Python sandbox using a killable subprocess.

    Executes short Python snippets inside a restricted namespace.  The
    subprocess approach (vs. threading) is the only way to enforce a hard
    wall-clock timeout on CPU-bound code: `os.kill(SIGKILL)` actually
    terminates the child — a thread-based approach cannot.

    Security model:
    - Child process runs code in an isolated dict namespace.
    - Built-ins are restricted to a safe allow-list; open(), __import__(),
      exec(), eval() are blocked unless explicitly permitted.
    - stdout/stderr are captured via StringIO redirect.
    - The child is spawned (not forked) so it starts with a clean interpreter
      state — no inherited locks, file descriptors, or parent state.
    - A wall-clock timeout is enforced with Process.join(timeout) + kill().
    - resource.setrlimit() is applied inside the child, so limits are
      correctly scoped to that one snippet (not the whole parent process).

    Usage:
        ex = SandboxedExecutor()
        ok, output = ex.run("print('hello')")
        ex2 = SandboxedExecutor(timeout=5, allow_imports=["math", "json"])
        ok, output = ex2.run("import math; print(math.pi)")
    """

    _SAFE_BUILTINS = {
        "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
        "callable", "chr", "complex", "dict", "dir", "divmod", "enumerate",
        "filter", "float", "format", "frozenset", "getattr", "globals",
        "hasattr", "hash", "hex", "id", "input", "int", "isinstance",
        "issubclass", "iter", "len", "list", "locals", "map", "max", "min",
        "next", "object", "oct", "ord", "pow", "print", "property", "range",
        "repr", "reversed", "round", "set", "setattr", "slice", "sorted",
        "staticmethod", "str", "sum", "super", "tuple", "type", "vars", "zip",
        "True", "False", "None", "NotImplemented", "Ellipsis",
        "ArithmeticError", "AssertionError", "AttributeError", "BaseException",
        "BlockingIOError", "BrokenPipeError", "BufferError", "BytesWarning",
        "ChildProcessError", "ConnectionAbortedError", "ConnectionError",
        "ConnectionRefusedError", "ConnectionResetError", "DeprecationWarning",
        "EOFError", "EnvironmentError", "Exception", "FileExistsError",
        "FileNotFoundError", "FloatingPointError", "FutureWarning", "GeneratorExit",
        "IOError", "ImportError", "ImportWarning", "IndentationError", "IndexError",
        "InterruptedError", "IsADirectoryError", "KeyError", "KeyboardInterrupt",
        "LookupError", "MemoryError", "ModuleNotFoundError", "NameError",
        "NotADirectoryError", "NotImplementedError", "OSError", "OverflowError",
        "PendingDeprecationWarning", "PermissionError", "ProcessLookupError",
        "RecursionError", "ReferenceError", "ResourceWarning", "RuntimeError",
        "RuntimeWarning", "StopAsyncIteration", "StopIteration", "SyntaxError",
        "SyntaxWarning", "SystemError", "SystemExit", "TabError", "TimeoutError",
        "TypeError", "UnboundLocalError", "UnicodeDecodeError", "UnicodeEncodeError",
        "UnicodeError", "UnicodeTranslateError", "UnicodeWarning", "UserWarning",
        "ValueError", "Warning", "ZeroDivisionError",
    }

    def __init__(self, timeout: float = 15.0,
                 allow_imports: list[str] | None = None,
                 max_output_bytes: int = 65536) -> None:
        self._timeout         = timeout
        self._allow_imports   = list(allow_imports or [])
        self._max_output      = max_output_bytes

    # ── public API ─────────────────────────────────────────────────────────────

    def run(self, code: str) -> tuple[bool, str]:
        """
        Execute *code* in a restricted sandbox subprocess.

        Returns ``(success: bool, output: str)``.

        The child process is SIGKILLed when the timeout fires, which
        guarantees it terminates — unlike a thread-based approach which can
        only stop the *caller* from waiting while the loop continues forever.
        ``daemon=True`` on the Process ensures the parent can exit even in
        the unlikely event that kill+join takes longer than expected.
        """
        code = textwrap.dedent(code)
        ctx  = _mp.get_context("spawn")   # clean interpreter; safe with threads
        q: _mp.Queue = ctx.Queue()        # type: ignore[type-arg]
        proc = ctx.Process(
            target=_sandbox_worker,
            args=(code, q, self._allow_imports, self._max_output,
                  set(self._SAFE_BUILTINS)),
            daemon=True,
        )
        proc.start()
        proc.join(timeout=self._timeout)

        if proc.is_alive():
            proc.kill()                   # SIGKILL — OS-guaranteed termination
            proc.join(timeout=2)
            return False, f"[SANDBOX2 TIMEOUT: code exceeded {self._timeout:.0f}s]"

        try:
            return q.get_nowait()
        except Exception:
            ok = proc.exitcode == 0
            return ok, "[SANDBOX2 ERROR: process exited without producing output]"

    def hash_code(self, code: str) -> str:
        """Stable SHA-256 fingerprint of the code for deduplication/caching."""
        return hashlib.sha256(code.encode()).hexdigest()


# ── subprocess worker (module-level so it is picklable under spawn) ────────────

def _sandbox_worker(
    code: str,
    q: "_mp.Queue[tuple[bool, str]]",
    allow_imports: list[str],
    max_output: int,
    safe_builtin_names: set[str],
) -> None:
    """
    Entry point for the sandboxed child process.
    Runs in a fresh interpreter (spawned, not forked).
    resource limits here apply only to this child — not the parent.
    """
    _apply_resource_limits()

    import io as _io, sys as _sys, builtins as _builtins_mod

    raw_builtins = vars(_builtins_mod)
    safe_builtins: dict[str, Any] = {
        k: raw_builtins[k]
        for k in safe_builtin_names
        if k in raw_builtins
    }

    if allow_imports:
        import importlib as _il
        _allowed = set(allow_imports)

        def _safe_import(name: str, *a: Any, **kw: Any) -> Any:
            if name.split(".")[0] not in _allowed:
                raise ImportError(
                    f"[SANDBOX2] import '{name}' is not in the allow-list: "
                    f"{sorted(_allowed)}")
            return _il.import_module(name)

        safe_builtins["__import__"] = _safe_import

    namespace: dict[str, Any] = {"__builtins__": safe_builtins}
    buf = _io.StringIO()
    old_out, old_err = _sys.stdout, _sys.stderr
    _sys.stdout = _sys.stderr = buf
    try:
        exec(compile(code, "<sandbox>", "exec"), namespace)   # nosec
        success = True
    except Exception as exc:
        print(f"[SANDBOX2 EXEC ERROR: {type(exc).__name__}: {exc}]")
        success = False
    finally:
        _sys.stdout = old_out
        _sys.stderr = old_err

    output = buf.getvalue()
    if len(output) > max_output:
        output = output[:max_output] + "\n[SANDBOX2: output truncated]"
    q.put((success, output))


def _apply_resource_limits() -> None:
    try:
        import resource                               # POSIX only
        resource.setrlimit(resource.RLIMIT_AS,    (256 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU,   (10, 10))
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    except Exception:
        pass  # Windows / unavailable — best-effort only
