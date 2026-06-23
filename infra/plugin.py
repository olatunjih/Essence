""" — hot-loadable Python plugins."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
import ast

# — hot-loadable Python plugins.
# [This module was empty — a content-loss gap from the build_pkg.py split.
#  Implemented fresh against tests/test_infra.py's expected contract:
#  plugin_tool() decorator + _plugin_registry, and PluginLoader.scan() /
#  PluginLoader._ast_check() that blocks dangerous imports via AST inspection
#  before a plugin file is ever executed.]

_plugin_registry: dict[str, "Callable[[dict], Any]"] = {}

_PLUGIN_BLOCKED_IMPORTS = frozenset((
    "subprocess", "socket", "ctypes", "multiprocessing", "pty", "fcntl",
))


def plugin_tool(name: str):
    """Decorator: register a function as a plugin-provided tool handler."""
    def _decorator(fn):
        _plugin_registry[name] = fn
        return fn
    return _decorator


class PluginLoader:
    """Scans a directory for `*.py` plugin files, AST-checks each for
    dangerous imports before execution, then executes safe ones so their
    top-level `@plugin_tool(...)` registrations populate _plugin_registry."""

    def __init__(self, plugin_dir: Path, poll_interval: int = 60):
        self.plugin_dir     = Path(plugin_dir)
        self.poll_interval  = poll_interval
        self._loaded: set[str] = set()

    def _ast_check(self, path: Path) -> tuple[bool, str]:
        """Static safety check: reject plugins that import dangerous modules.
        Returns (safe, reason) — reason is empty when safe."""
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"could not parse plugin: {e}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in _PLUGIN_BLOCKED_IMPORTS:
                        return False, f"blocked import: {top}"
            elif isinstance(node, ast.ImportFrom):
                top = (node.module or "").split(".")[0]
                if top in _PLUGIN_BLOCKED_IMPORTS:
                    return False, f"blocked import: {top}"
        return True, ""

    def scan(self) -> list[str]:
        """Scan plugin_dir for *.py files, AST-check, then exec safe ones.
        Returns the list of newly-loaded plugin file stems."""
        loaded_now: list[str] = []
        if not self.plugin_dir.is_dir():
            return loaded_now
        for path in sorted(self.plugin_dir.glob("*.py")):
            stem = path.stem
            if stem in self._loaded:
                continue
            safe, reason = self._ast_check(path)
            if not safe:
                log.warning("plugin_rejected", extra={"file": str(path), "reason": reason})
                continue
            try:
                ns: dict = {"plugin_tool": plugin_tool}
                exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
                self._loaded.add(stem)
                loaded_now.append(stem)
            except Exception as e:
                log.warning("plugin_load_failed", extra={"file": str(path), "error": str(e)})
        return loaded_now



