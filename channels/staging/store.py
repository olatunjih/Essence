
"""Staging store: holds pre-commit artifacts until guardrail clears them."""
from __future__ import annotations
import json, time
from pathlib import Path


class StagingStore:
    """
    File-backed staging area for PMP artifacts.
    Items are held until commit() or rollback() is called.
    """

    def __init__(self, base_dir: str = "staging") -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._staged: dict[str, dict] = {}

    def stage(self, key: str, content: object) -> None:
        """Stage a named artifact."""
        self._staged[key] = {"key": key, "content": content, "ts": time.time()}

    def commit(self) -> dict[str, object]:
        """Commit all staged artifacts; returns them and clears staging."""
        result = {k: v["content"] for k, v in self._staged.items()}
        self._staged.clear()
        return result

    def rollback(self) -> None:
        """Discard all staged artifacts."""
        self._staged.clear()

    def list_staged(self) -> list[str]:
        return list(self._staged.keys())
