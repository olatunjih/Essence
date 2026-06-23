"""Thin uvicorn entrypoint for smoke-testing and direct launch.

Usage:
    uvicorn essence.server._run:app --reload
"""
from pathlib import Path
from essence.server.api import create_app

_WORKSPACE = Path.home() / ".essence" / "workspace"
_WORKSPACE.mkdir(parents=True, exist_ok=True)

app = create_app(_WORKSPACE)
