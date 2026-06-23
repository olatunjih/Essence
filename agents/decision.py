"""DecisionQueue: human-in-the-loop structured approvals."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# DECISION QUEUE
# ══════════════════════════════════════════════════════════════════════════════
# DecisionQueue replaces the current binary [y/N] prompt with a proper
# decision management system:
#   • Pending decisions accumulate in a file-backed queue
#   • Urgency is auto-classified per tool + arg heuristics
#   • Server UI can batch-approve/reject via /api/decisions endpoint
#   • Decisions expire and auto-escalate if not acted upon
#   • Non-blocking: agent continues planning while awaiting approval

class DecisionPriority(_enum.Enum):
    INFO     = 0
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4


@_dc.dataclass
class Decision:
    decision_id: str
    tool_name:   str
    args:        dict
    priority:    DecisionPriority
    reason:      str
    created_at:  float
    expires_at:  float
    approved:    bool | None = None   # None = pending
    rejected_reason: str     = ""
    session_id:  str         = ""

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "tool_name":   self.tool_name,
            "args":        self.args,
            "priority":    self.priority.value,
            "reason":      self.reason,
            "created_at":  self.created_at,
            "expires_at":  self.expires_at,
            "approved":    self.approved,
            "rejected_reason": self.rejected_reason,
            "session_id":  self.session_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Decision":
        dd = cls(
            decision_id=d["decision_id"],
            tool_name=d["tool_name"],
            args=d.get("args", {}),
            priority=DecisionPriority(d.get("priority", 1)),
            reason=d.get("reason", ""),
            created_at=d.get("created_at", 0.0),
            expires_at=d.get("expires_at", 0.0),
            session_id=d.get("session_id", ""),
        )
        dd.approved         = d.get("approved")
        dd.rejected_reason  = d.get("rejected_reason", "")
        return dd


class DecisionQueue:
    """
    File-backed decision queue for human-in-the-loop approvals.

    The agent enqueues a Decision for any tool call that requires human
    approval (based on CapabilityPolicy). It then either:
      a) Blocks (interactive mode) — polls with a timeout
      b) Continues planning and re-checks on next heartbeat (server mode)

    The web UI reads /api/decisions and POSTs approve/reject.
    """

    _PRIORITY_RULES: dict[str, DecisionPriority] = {
        "shell":       DecisionPriority.HIGH,
        "python_exec": DecisionPriority.HIGH,
        "write_file":  DecisionPriority.MEDIUM,
        "train_model": DecisionPriority.MEDIUM,
        "finetune":    DecisionPriority.HIGH,
        "ingest":      DecisionPriority.LOW,
        "web_search":  DecisionPriority.INFO,
        "read_file":   DecisionPriority.INFO,
    }
    _DEFAULT_TTL    = 300.0   # 5 min default
    _CRITICAL_TTL   = 60.0    # 1 min for critical decisions

    def __init__(self, workspace: Path, default_ttl: float = 300.0) -> None:
        self._path       = workspace / "logs" / "decisions.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock       = threading.Lock()
        self._default_ttl = default_ttl
        self._cache: dict[str, Decision] = {}
        self._load()

    _MAX_CACHE = 500  # LRU cap; stale decisions pruned on load

    def _load(self) -> None:
        if not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                d = Decision.from_dict(json.loads(line))
                self._cache[d.decision_id] = d
            except Exception:
                pass
        # Prune expired + enforce size cap
        _now = time.monotonic()
        for k in [k for k, d in list(self._cache.items())
                  if _now > getattr(d, "expires_at", _now) + 120]:
            self._cache.pop(k, None)
        if len(self._cache) > self._MAX_CACHE:
            for k in list(self._cache)[:len(self._cache) - self._MAX_CACHE]:
                self._cache.pop(k, None)

    def _flush(self) -> None:
        """Rewrite the decision log from cache."""
        lines = [json.dumps(d.to_dict()) for d in self._cache.values()]
        self._path.write_text("\n".join(lines) + ("\n" if lines else ""),
                               encoding="utf-8")

    def classify_priority(self, tool_name: str, args: dict) -> DecisionPriority:
        base = self._PRIORITY_RULES.get(tool_name, DecisionPriority.MEDIUM)
        # Elevate destructive shell commands to CRITICAL
        if tool_name == "shell":
            cmd = args.get("command", "")
            if any(k in cmd for k in ("rm ", "drop ", "delete ", "format ",
                                       "mkfs", "dd if=", "> /dev/")):
                return DecisionPriority.CRITICAL
        return base

    def enqueue(self, tool_name: str, args: dict,
                reason: str = "", session_id: str = "") -> Decision:
        priority = self.classify_priority(tool_name, args)
        ttl      = (self._CRITICAL_TTL
                    if priority == DecisionPriority.CRITICAL
                    else self._default_ttl)
        d = Decision(
            decision_id = secrets.token_hex(8),
            tool_name   = tool_name,
            args        = args,
            priority    = priority,
            reason      = reason or f"Requires approval: {tool_name}",
            created_at  = time.time(),
            expires_at  = time.time() + ttl,
            session_id  = session_id,
        )
        with self._lock:
            self._cache[d.decision_id] = d
            self._flush()
        log.info("decision_enqueued",
                 extra={"id": d.decision_id, "tool": tool_name,
                         "priority": priority.name})
        return d

    def pending(self) -> list[Decision]:
        now = time.time()
        with self._lock:
            return [d for d in self._cache.values()
                    if d.approved is None and d.expires_at > now]

    def list_pending(self) -> list[dict]:
        """Return pending decisions as dicts (for JSON API serialisation)."""
        return [d.to_dict() for d in self.pending()]

    def list_all(self) -> list[dict]:
        """Return all decisions (pending + resolved) as dicts."""
        with self._lock:
            return [d.to_dict() for d in self._cache.values()]

    def approve(self, decision_id: str) -> bool:
        with self._lock:
            d = self._cache.get(decision_id)
            if d is None or d.approved is not None:
                return False
            d.approved = True
            self._flush()
        return True

    def reject(self, decision_id: str, reason: str = "") -> bool:
        with self._lock:
            d = self._cache.get(decision_id)
            if d is None or d.approved is not None:
                return False
            d.approved         = False
            d.rejected_reason  = reason
            self._flush()
        return True

    def batch_approve(self, decision_ids: list[str]) -> int:
        return sum(1 for did in decision_ids if self.approve(did))

    def wait_for(self, decision_id: str,
                 timeout: float = 60.0, poll_interval: float = 1.0
                 ) -> bool | None:
        """Block up to timeout seconds for a decision. Returns approved/None."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                d = self._cache.get(decision_id)
            if d and d.approved is not None:
                return d.approved
            time.sleep(poll_interval)
        return None   # timed out

    async def await_for(self, decision_id: str,
                       timeout: float = 60.0, poll_interval: float = 1.0
                       ) -> bool | None:
        """Async version of wait_for()."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                d = self._cache.get(decision_id)
            if d and d.approved is not None:
                return d.approved
            await asyncio.sleep(poll_interval)
        return None


# ══════════════════════════════════════════════════════════════════════════════
