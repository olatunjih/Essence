"""
SubagentRouter — delegates tasks to peer A2A agents.

Reads the agent directory from <workspace>/config/agent_directory.json.
Selects the best peer for a given task type based on skill coverage and load.
Delegates via A2AClient.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.routing.subagent_router")


@dataclasses.dataclass
class PeerAgent:
    """A known peer agent in the agent directory."""
    name:        str
    a2a_url:     str
    skills:      list[str]
    current_load: float = 0.0   # 0.0 = idle, 1.0 = saturated
    last_seen:   float = 0.0


class SubagentRouter:
    """
    Routes tasks to the best available peer agent via A2A.

    The agent directory is a JSON file at <workspace>/config/agent_directory.json
    listing known peer agents with their A2A URLs, skill lists, and load.
    """

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace
        self._peers: list[PeerAgent] = self._load_directory(workspace)

    def _load_directory(self, workspace: Path | None) -> list[PeerAgent]:
        if workspace is None:
            return []
        dir_path = workspace / "config" / "agent_directory.json"
        if not dir_path.exists():
            return []
        try:
            raw = json.loads(dir_path.read_text(encoding="utf-8"))
            peers = []
            for entry in raw.get("agents", []):
                peers.append(PeerAgent(
                    name=entry["name"],
                    a2a_url=entry["a2a_url"],
                    skills=entry.get("skills", []),
                    current_load=float(entry.get("current_load", 0.0)),
                    last_seen=float(entry.get("last_seen", 0.0)),
                ))
            log.info("subagent_directory_loaded",
                     extra={"peers": len(peers)})
            return peers
        except Exception as exc:
            log.warning("subagent_directory_load_error",
                        extra={"error": str(exc)[:120]})
            return []

    def find_peer(self, task_type: str) -> PeerAgent | None:
        """Find the best peer for the given task type (lowest load, has skill)."""
        candidates = [
            p for p in self._peers
            if task_type in p.skills or not p.skills
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda p: p.current_load)

    async def delegate(self, task: Any, task_type: str = "") -> dict:
        """
        Delegate a task to the best available peer agent.

        Returns the peer's response dict, or raises RuntimeError if no
        suitable peer is found.
        """
        peer = self.find_peer(task_type)
        if peer is None:
            raise RuntimeError(
                f"No peer agent available for task type '{task_type}'. "
                f"Known peers: {[p.name for p in self._peers]}"
            )

        try:
            from essence.protocols.a2a import A2AClient
            import asyncio
            client  = A2AClient(base_url=peer.a2a_url)
            message = getattr(task, "goal", str(task))
            loop    = asyncio.get_event_loop()
            result  = await loop.run_in_executor(
                None, client.send_task, message)
            log.info("subagent_delegated",
                     extra={"peer": peer.name, "task_type": task_type})
            return {"peer": peer.name, "result": result}
        except Exception as exc:
            log.warning("subagent_delegation_failed",
                        extra={"peer": peer.name, "error": str(exc)[:120]})
            raise

    def list_peers(self) -> list[dict]:
        """Return a serializable list of all known peers."""
        return [
            {
                "name":         p.name,
                "a2a_url":      p.a2a_url,
                "skills":       p.skills,
                "current_load": p.current_load,
                "last_seen":    p.last_seen,
            }
            for p in self._peers
        ]
