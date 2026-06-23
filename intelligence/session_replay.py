"""SessionReplayEngine — structured session snapshots with replay and branch support.

Stores a snapshot of each session (decisions made, goals updated, tools used,
outcomes) and allows replaying or branching from any past session point.

Writes to <workspace>/sessions/<session_id>.json
Reads from CapsuleRepo + EpisodicStore; exposes replay() and branch().
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.intelligence.session_replay")


@dataclasses.dataclass
class SessionStep:
    """One recorded step within a session."""
    step_index:  int
    timestamp:   float
    kind:        str         # "user_message" | "tool_call" | "decision" | "goal_update" | "response"
    content:     str         # human-readable description
    metadata:    dict        # extra structured data (tool args, decision rationale, etc.)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionStep":
        return cls(**d)


@dataclasses.dataclass
class SessionSnapshot:
    """Full structured snapshot of one session."""
    session_id:    str
    started_at:    float
    ended_at:      float | None
    steps:         list[SessionStep]
    capsule_ids:   list[str]
    goals_updated: list[str]
    tools_used:    list[str]
    outcome:       str         # "complete" | "partial" | "abandoned"
    parent_id:     str | None  # set when this session is a branch

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["steps"] = [s.to_dict() for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SessionSnapshot":
        steps = [SessionStep.from_dict(s) for s in d.get("steps", [])]
        return cls(
            session_id    = d["session_id"],
            started_at    = d["started_at"],
            ended_at      = d.get("ended_at"),
            steps         = steps,
            capsule_ids   = d.get("capsule_ids", []),
            goals_updated = d.get("goals_updated", []),
            tools_used    = d.get("tools_used", []),
            outcome       = d.get("outcome", "partial"),
            parent_id     = d.get("parent_id"),
        )


class SessionReplayEngine:
    """
    Records, replays, and branches sessions.

    Integration points
    ------------------
    Boot       : instantiated after EpisodicStore and CapsuleRepo
    ingest_capsule : call record_step(...) for each user message, tool call, etc.
    Heartbeat  : call close_session() at end of idle period to finalise snapshot

    Session lifecycle
    -----------------
    1. open_session()   — called at start of each user interaction session
    2. record_step()    — called for each significant event during the session
    3. close_session()  — called when session ends; writes snapshot to disk
    4. replay()         — re-plays a past session as a structured log
    5. branch()         — creates a new session branched from a past point
    """

    def __init__(self, workspace: Path,
                 episodic: Any = None,
                 capsule_repo: Any = None) -> None:
        self._ws          = workspace
        self._sessions_dir = workspace / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._episodic    = episodic
        self._capsule_repo = capsule_repo
        self._active: SessionSnapshot | None = None
        self._index_path  = workspace / "sessions" / "_index.json"

    # ── session lifecycle ─────────────────────────────────────────────────────

    def open_session(self, session_id: str | None = None,
                     parent_id: str | None = None) -> str:
        """Open a new session and return its ID."""
        sid = session_id or str(uuid.uuid4())[:16]
        self._active = SessionSnapshot(
            session_id    = sid,
            started_at    = time.time(),
            ended_at      = None,
            steps         = [],
            capsule_ids   = [],
            goals_updated = [],
            tools_used    = [],
            outcome       = "partial",
            parent_id     = parent_id,
        )
        log.debug("session_opened", extra={"session_id": sid})
        return sid

    def record_step(self, kind: str, content: str,
                    metadata: dict | None = None,
                    capsule_id: str | None = None,
                    tool_name: str | None = None,
                    goal: str | None = None) -> None:
        """
        Record one step in the active session.

        Args:
            kind        : step type ("user_message" | "tool_call" | "decision" |
                          "goal_update" | "response")
            content     : human-readable description of what happened
            metadata    : optional structured data
            capsule_id  : if this step relates to a capsule, record it
            tool_name   : if a tool was called, record its name
            goal        : if a goal was updated, record it
        """
        if self._active is None:
            self.open_session()
        assert self._active is not None

        step = SessionStep(
            step_index = len(self._active.steps),
            timestamp  = time.time(),
            kind       = kind,
            content    = content[:500],
            metadata   = metadata or {},
        )
        self._active.steps.append(step)

        if capsule_id and capsule_id not in self._active.capsule_ids:
            self._active.capsule_ids.append(capsule_id)
        if tool_name and tool_name not in self._active.tools_used:
            self._active.tools_used.append(tool_name)
        if goal and goal not in self._active.goals_updated:
            self._active.goals_updated.append(goal[:120])

    def close_session(self, outcome: str = "complete") -> str | None:
        """
        Finalise the active session, write snapshot to disk, update index.
        Returns the session_id, or None if no active session.
        """
        if self._active is None:
            return None
        snap = self._active
        snap.ended_at = time.time()
        snap.outcome  = outcome
        self._active  = None

        # Also record in EpisodicStore for searchability
        if self._episodic is not None:
            try:
                summary = (
                    f"Session {snap.session_id}: "
                    f"{len(snap.steps)} steps, "
                    f"tools={snap.tools_used}, "
                    f"outcome={snap.outcome}"
                )
                self._episodic.record(
                    text       = summary,
                    session_id = snap.session_id,
                    metadata   = {"type": "session_snapshot", "outcome": snap.outcome},
                )
            except Exception as exc:
                log.debug("session_episodic_record_error: %s", exc)

        path = self._session_path(snap.session_id)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(snap.to_dict(), indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            log.warning("session_write_error: %s", exc)
            return snap.session_id

        self._update_index(snap)
        log.info("session_closed",
                 extra={"session_id": snap.session_id,
                        "steps": len(snap.steps),
                        "outcome": outcome})
        return snap.session_id

    # ── replay + branch ───────────────────────────────────────────────────────

    def replay(self, session_id: str,
               from_step: int = 0) -> list[SessionStep]:
        """
        Return the steps of a past session from from_step onward.
        Raises FileNotFoundError if the session does not exist.
        """
        snap = self._load_session(session_id)
        steps = [s for s in snap.steps if s.step_index >= from_step]
        log.info("session_replayed",
                 extra={"session_id": session_id,
                        "from_step": from_step,
                        "steps": len(steps)})
        return steps

    def branch(self, session_id: str, from_step: int) -> str:
        """
        Create a new session branched from `session_id` at `from_step`.
        The new session inherits all steps up to from_step and is opened
        as the active session.  Returns the new session_id.
        """
        snap  = self._load_session(session_id)
        new_id = str(uuid.uuid4())[:16]
        branched_steps = [
            SessionStep(**{**s.to_dict(), "step_index": i})
            for i, s in enumerate(snap.steps[:from_step])
        ]
        self._active = SessionSnapshot(
            session_id    = new_id,
            started_at    = time.time(),
            ended_at      = None,
            steps         = branched_steps,
            capsule_ids   = list(snap.capsule_ids),
            goals_updated = list(snap.goals_updated),
            tools_used    = list(snap.tools_used),
            outcome       = "partial",
            parent_id     = session_id,
        )
        log.info("session_branched",
                 extra={"parent": session_id,
                        "new_session": new_id,
                        "from_step": from_step})
        return new_id

    # ── listing ───────────────────────────────────────────────────────────────

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """Return recent session summaries from the index."""
        index = self._load_index()
        sessions = sorted(index.values(),
                          key=lambda s: s.get("started_at", 0), reverse=True)
        return sessions[:limit]

    def active_session_id(self) -> str | None:
        """Return the ID of the currently active session, if any."""
        return self._active.session_id if self._active else None

    # ── internal helpers ──────────────────────────────────────────────────────

    def _session_path(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_").replace("..", "_")
        return self._sessions_dir / f"{safe}.json"

    def _load_session(self, session_id: str) -> SessionSnapshot:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return SessionSnapshot.from_dict(data)

    def _load_index(self) -> dict:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _update_index(self, snap: SessionSnapshot) -> None:
        index = self._load_index()
        index[snap.session_id] = {
            "session_id":  snap.session_id,
            "started_at":  snap.started_at,
            "ended_at":    snap.ended_at,
            "steps":       len(snap.steps),
            "tools_used":  snap.tools_used[:5],
            "outcome":     snap.outcome,
            "parent_id":   snap.parent_id,
        }
        # Keep index bounded to last 500 sessions
        if len(index) > 500:
            oldest = sorted(index.items(),
                           key=lambda x: x[1].get("started_at", 0))
            for sid, _ in oldest[:len(index) - 500]:
                del index[sid]
        try:
            tmp = self._index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(index, indent=2), encoding="utf-8")
            tmp.replace(self._index_path)
        except Exception as exc:
            log.debug("session_index_write_error: %s", exc)
