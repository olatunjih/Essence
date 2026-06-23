"""Canonical workspace_root — single source of truth.

All modules that need the workspace root must import from here.
DO NOT define _workspace_root() / workspace_root() anywhere else.
"""
from __future__ import annotations
import os
import platform
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def workspace_root() -> Path:
    """Return the Essence workspace root directory.

    Resolution order:
      1. ``Essence_WORKSPACE`` environment variable
      2. ``~/.essence``  (Linux / macOS)
      3. ``%APPDATA%\\Essence``  (Windows)
    """
    env = os.environ.get("Essence_WORKSPACE") or os.environ.get("ESSENCE_WORKSPACE")
    if env:
        return Path(env)
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "Essence"
    return Path.home() / ".essence"


def ensure_workspace() -> Path:
    """Return workspace_root(), creating it if it does not exist."""
    ws = workspace_root()
    ws.mkdir(parents=True, exist_ok=True)
    return ws
