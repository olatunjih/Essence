"""Protocol kernel: multi-agent communication fabric."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# PROTOCOL KERNEL
# ══════════════════════════════════════════════════════════════════════════════

class MessageProtocol:
    """Enforces typed, versioned message schemas for multi-agent communication."""
    def __init__(self, version: str = "1.0"):
        self.version = version

    def wrap(self, sender: str, receiver: str,
             msg_type: str, content: Any) -> dict:
        """Wraps content in a versioned, typed message envelope."""
        return {
            "v":       self.version,
            "ts":      time.time(),
            "from":    sender,
            "to":      receiver,
            "type":    msg_type,
            "payload": content
        }

    def validate(self, msg: dict) -> bool:
        """Validates message envelope integrity."""
        required = {"v", "ts", "from", "to", "type", "payload"}
        return all(k in msg for k in required)

class TaskHandoff:
    """Manages task lifecycle with claim, lock, and transfer semantics."""
    def __init__(self):
        self._locks: dict[str, str] = {} # task_id -> agent_id
        self._lock = threading.Lock()

    def claim(self, task_id: str, agent_id: str) -> bool:
        """Claims a task for an agent. Returns True if successful."""
        with self._lock:
            if task_id in self._locks: return False
            self._locks[task_id] = agent_id
            return True

    def release(self, task_id: str, agent_id: str):
        """Releases a task lock."""
        with self._lock:
            if self._locks.get(task_id) == agent_id:
                del self._locks[task_id]

    def transfer(self, task_id: str, from_agent: str, to_agent: str) -> bool:
        """Transfers a task lock from one agent to another."""
        with self._lock:
            if self._locks.get(task_id) != from_agent: return False
            self._locks[task_id] = to_agent
            return True

class ConflictResolution:
    """Resolves multi-agent disagreements via voting and arbitration."""
    def resolve_by_voting(self, options: dict[str, int]) -> str:
        """Simple plurality vote resolution."""
        if not options: return ""
        return max(options, key=options.get)

    def arbitrate(self, proposal_a: str, proposal_b: str,
                  priority_a: int, priority_b: int) -> str:
        """Arbitrates between two proposals based on agent priority."""
        if priority_a >= priority_b:
            return proposal_a
        return proposal_b

# ══════════════════════════════════════════════════════════════════════════════
