"""CapabilityRetirementManager — lifecycle governance for learned skills/strategies.

Applies the same ACTIVE → SUSPECT → QUARANTINED → RETIRED | REHABILITATED
state machine that tools/registry.py uses for tool records, to the learned
skills and procedures stored in workspace/skill_system.py.

Wire into HeartbeatScheduler as a weekly job (retirement evaluation is expensive).
"""
from __future__ import annotations
import dataclasses
import json
import logging
import time
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.capability.retirement")


class CapabilityState(str, Enum):
    ACTIVE       = "ACTIVE"
    SUSPECT      = "SUSPECT"
    QUARANTINED  = "QUARANTINED"
    RETIRED      = "RETIRED"
    REHABILITATED = "REHABILITATED"


@dataclasses.dataclass
class CapabilityRecord:
    skill_id:        str
    state:           CapabilityState = CapabilityState.ACTIVE
    success_count:   int   = 0
    failure_count:   int   = 0
    last_used_at:    float = dataclasses.field(default_factory=time.time)
    created_at:      float = dataclasses.field(default_factory=time.time)
    retired_reason:  str   = ""
    quarantine_until: float = 0.0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return (self.success_count / total) if total > 0 else 1.0

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityRecord":
        d = dict(d)
        d["state"] = CapabilityState(d.get("state", "ACTIVE"))
        return cls(**d)


class CapabilityRetirementManager:
    """
    Governs the lifecycle of learned skills/strategies.

    Retirement triggers:
      - Success rate < SUCCESS_THRESHOLD over a 30-day window
      - Not used for > STALE_DAYS
      - Explicitly superseded by a higher-performing variant

    Persist state to workspace/capability_lifecycle.json.
    """

    STALE_DAYS         = 60
    SUCCESS_THRESHOLD  = 0.35
    SUSPECT_THRESHOLD  = 0.50
    QUARANTINE_DAYS    = 7

    def __init__(self, workspace: Path, skill_system: Any = None) -> None:
        self._ws           = workspace
        self._skill_system = skill_system
        self._store_path   = workspace / "capability_lifecycle.json"
        self._records: dict[str, CapabilityRecord] = self._load()

    # ── persistence ─────────────────────────────────────────────────────────

    def _load(self) -> dict[str, CapabilityRecord]:
        if self._store_path.exists():
            try:
                raw = json.loads(self._store_path.read_text(encoding="utf-8"))
                return {k: CapabilityRecord.from_dict(v) for k, v in raw.items()}
            except Exception as exc:
                log.warning("capability_retirement_load_error: %s", exc)
        return {}

    def _save(self) -> None:
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            self._store_path.write_text(
                json.dumps(
                    {k: v.to_dict() for k, v in self._records.items()},
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("capability_retirement_save_error: %s", exc)

    # ── public API ──────────────────────────────────────────────────────────

    def record_outcome(self, skill_id: str, success: bool) -> None:
        """Record a skill execution outcome (call after each task execution)."""
        rec = self._records.setdefault(
            skill_id, CapabilityRecord(skill_id=skill_id))
        if success:
            rec.success_count += 1
        else:
            rec.failure_count += 1
        rec.last_used_at = time.time()
        self._save()

    def evaluate_all(self) -> dict[str, str]:
        """
        Run retirement evaluation over all tracked skills.
        Returns mapping of skill_id → new_state (only changed records).
        """
        changes: dict[str, str] = {}
        now = time.time()
        stale_cutoff = now - self.STALE_DAYS * 86400

        # Sync with skill_system if available
        if self._skill_system is not None:
            try:
                known_ids: list[str] = list(
                    getattr(self._skill_system, "list_skills", lambda: [])())
                for sid in known_ids:
                    self._records.setdefault(sid, CapabilityRecord(skill_id=sid))
            except Exception as exc:
                log.debug("capability_skill_system_sync_error: %s", exc)

        for skill_id, rec in list(self._records.items()):
            old_state = rec.state

            if rec.state == CapabilityState.RETIRED:
                continue

            # Release quarantine if period elapsed
            if (rec.state == CapabilityState.QUARANTINED
                    and rec.quarantine_until > 0
                    and now > rec.quarantine_until):
                rec.state = CapabilityState.SUSPECT

            # Stale check
            if rec.last_used_at < stale_cutoff and rec.state == CapabilityState.ACTIVE:
                self.quarantine(skill_id, reason="not_used_stale")
            # Performance check
            elif rec.success_rate < self.SUCCESS_THRESHOLD and rec.state in (
                    CapabilityState.ACTIVE, CapabilityState.SUSPECT):
                self.retire(skill_id, reason=f"low_success_rate={rec.success_rate:.2f}")
            elif (rec.success_rate < self.SUSPECT_THRESHOLD
                    and rec.state == CapabilityState.ACTIVE):
                self.quarantine(skill_id, reason=f"declining_success_rate={rec.success_rate:.2f}")

            if rec.state != old_state:
                changes[skill_id] = rec.state.value
                log.info(
                    "capability_state_change",
                    extra={
                        "skill_id":  skill_id,
                        "old_state": old_state.value,
                        "new_state": rec.state.value,
                    },
                )

        self._save()
        return changes

    def retire(self, skill_id: str, reason: str = "") -> None:
        """Mark a skill as permanently RETIRED."""
        rec = self._records.setdefault(
            skill_id, CapabilityRecord(skill_id=skill_id))
        rec.state          = CapabilityState.RETIRED
        rec.retired_reason = reason
        log.warning("capability_retired",
                    extra={"skill_id": skill_id, "reason": reason})
        self._save()
        if self._skill_system is not None:
            try:
                retire_fn = getattr(self._skill_system, "disable_skill", None)
                if retire_fn:
                    retire_fn(skill_id)
            except Exception as exc:
                log.debug("skill_system_disable_error: %s", exc)

    def quarantine(self, skill_id: str, reason: str = "") -> None:
        """Move a skill to QUARANTINED for QUARANTINE_DAYS."""
        rec = self._records.setdefault(
            skill_id, CapabilityRecord(skill_id=skill_id))
        rec.state            = CapabilityState.QUARANTINED
        rec.quarantine_until = time.time() + self.QUARANTINE_DAYS * 86400
        rec.retired_reason   = reason
        log.info("capability_quarantined",
                 extra={"skill_id": skill_id, "reason": reason})
        self._save()

    def rehabilitate(self, skill_id: str) -> None:
        """Move a skill from QUARANTINED/SUSPECT back to REHABILITATED (then ACTIVE)."""
        rec = self._records.get(skill_id)
        if rec is None:
            log.warning("rehabilitate: unknown skill %s", skill_id)
            return
        rec.state            = CapabilityState.REHABILITATED
        rec.quarantine_until = 0.0
        rec.retired_reason   = ""
        log.info("capability_rehabilitated", extra={"skill_id": skill_id})
        self._save()

    def get_state(self, skill_id: str) -> CapabilityState:
        rec = self._records.get(skill_id)
        return rec.state if rec else CapabilityState.ACTIVE

    def all_records(self) -> list[dict]:
        return [r.to_dict() for r in self._records.values()]
