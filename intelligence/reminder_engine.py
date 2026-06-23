"""ReminderEngine — schedules and fires time-based reminders tied to TemporalPlane goals.

Reads goals from TemporalPlane.all_goals(), lets users add explicit reminders,
and fires them into the event_bus on each heartbeat tick so ProactiveEngine can
surface them.

Persistence: <workspace>/identity/reminders.json
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.intelligence.reminder_engine")


@dataclasses.dataclass
class Reminder:
    """A single scheduled reminder."""
    id:          str
    title:       str
    body:        str
    fire_at:     float          # Unix timestamp — fire when time.time() >= fire_at
    recurrence:  str | None     # None | "daily" | "weekly"
    source:      str            # "user" | "goal" | "deadline"
    horizon:     str | None     # TemporalGoal horizon if goal-derived
    fired_at:    float | None   # Set when last fired; None = never fired
    enabled:     bool

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Reminder":
        return cls(**{k: v for k, v in d.items() if k in
                      {f.name for f in dataclasses.fields(cls)}})


_HORIZON_LEAD_SECONDS: dict[str, float] = {
    "TODAY":      3600 * 2,     # fire 2 h before end-of-day
    "THIS_WEEK":  3600 * 24,    # fire 1 day before end-of-week
    "THIS_MONTH": 3600 * 48,    # fire 2 days before end-of-month
    "THIS_YEAR":  3600 * 168,   # fire 1 week before end-of-year
    "FIVE_YEAR":  3600 * 720,   # fire 30 days before 5-year mark
}

_RECURRENCE_SECONDS: dict[str, float] = {
    "daily":  86400.0,
    "weekly": 86400.0 * 7,
}


class ReminderEngine:
    """
    Schedules and fires time-based reminders.

    Integration points
    ------------------
    Boot         : instantiated after TemporalPlane; receives event_bus reference
    Heartbeat    : call tick() on every heartbeat cycle to fire due reminders
    ingest_capsule : call sync_from_goals(temporal) after each user message to
                     auto-create reminders for new goals
    """

    def __init__(self, workspace: Path,
                 event_bus: Any = None,
                 proactive: Any = None) -> None:
        self._path      = workspace / "identity" / "reminders.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._bus       = event_bus
        self._proactive = proactive
        self._reminders: dict[str, Reminder] = self._load()

    # ── public API ────────────────────────────────────────────────────────────

    def add(self, title: str, body: str, fire_at: float,
            recurrence: str | None = None,
            source: str = "user",
            horizon: str | None = None) -> Reminder:
        """Add a new reminder.  Returns the created Reminder."""
        import secrets as _sec
        rid = _sec.token_hex(6)
        rem = Reminder(
            id=rid, title=title, body=body, fire_at=fire_at,
            recurrence=recurrence, source=source, horizon=horizon,
            fired_at=None, enabled=True,
        )
        self._reminders[rid] = rem
        self._save()
        log.info("reminder_added",
                 extra={"id": rid, "title": title,
                        "fire_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                  time.localtime(fire_at))})
        return rem

    def remove(self, reminder_id: str) -> bool:
        """Disable a reminder by ID. Returns True if found."""
        if reminder_id in self._reminders:
            self._reminders[reminder_id].enabled = False
            self._save()
            return True
        return False

    def list_reminders(self, enabled_only: bool = True) -> list[Reminder]:
        """Return current reminders, optionally filtered to enabled only."""
        rems = list(self._reminders.values())
        if enabled_only:
            rems = [r for r in rems if r.enabled]
        return sorted(rems, key=lambda r: r.fire_at)

    def sync_from_goals(self, temporal: Any) -> int:
        """
        Auto-create reminders for any TemporalGoal that lacks one.
        Returns count of new reminders created.
        """
        if temporal is None:
            return 0
        created = 0
        now = time.time()
        try:
            goals = temporal.all_goals()
        except Exception as exc:
            log.debug("reminder_sync_goals_error: %s", exc)
            return 0

        existing_horizons = {r.horizon for r in self._reminders.values()
                             if r.source == "goal" and r.enabled}
        for goal in goals:
            horizon     = goal.get("horizon", "")
            description = goal.get("description", "")
            if not horizon or not description:
                continue
            if horizon in existing_horizons:
                continue
            lead = _HORIZON_LEAD_SECONDS.get(horizon, 3600 * 24)
            fire_at = now + lead
            self.add(
                title=f"Goal reminder: {horizon}",
                body=description[:200],
                fire_at=fire_at,
                recurrence=None,
                source="goal",
                horizon=horizon,
            )
            existing_horizons.add(horizon)
            created += 1
        return created

    def tick(self) -> list[Reminder]:
        """
        Check all reminders and fire any that are due.
        Returns list of fired reminders.
        """
        now    = time.time()
        fired: list[Reminder] = []
        for rem in list(self._reminders.values()):
            if not rem.enabled:
                continue
            if now < rem.fire_at:
                continue
            # Already fired recently (within 60 s) — skip to prevent double-fire
            if rem.fired_at is not None and (now - rem.fired_at) < 60:
                continue
            self._fire(rem, now)
            fired.append(rem)
        if fired:
            self._save()
        return fired

    # ── internal helpers ──────────────────────────────────────────────────────

    def _fire(self, rem: Reminder, now: float) -> None:
        """Mark as fired and publish event."""
        rem.fired_at = now
        if rem.recurrence and rem.recurrence in _RECURRENCE_SECONDS:
            rem.fire_at = now + _RECURRENCE_SECONDS[rem.recurrence]
        else:
            rem.enabled = False   # one-shot

        log.info("reminder_fired",
                 extra={"id": rem.id, "title": rem.title, "source": rem.source})

        payload = {
            "reminder_id": rem.id,
            "title":       rem.title,
            "body":        rem.body,
            "source":      rem.source,
            "horizon":     rem.horizon,
        }

        # Publish into WebhookEventBus if available
        if self._bus is not None:
            try:
                from essence.agents.proactive import WebhookEvent
                evt = WebhookEvent(
                    source="reminder",
                    event_type="due",
                    payload=payload,
                )
                pub = getattr(self._bus, "publish", None)
                if callable(pub):
                    pub(evt)
            except Exception as exc:
                log.debug("reminder_bus_publish_error: %s", exc)

        # Write directly to proactive engine memory if bus unavailable
        if self._proactive is not None:
            try:
                mem = getattr(self._proactive, "_mem", None)
                if mem is not None:
                    import json as _json
                    key = f"_proactive_pending_{int(now * 1000)}"
                    mem.set(key, _json.dumps({
                        "type":     "reminder_due",
                        "title":    rem.title,
                        "body":     rem.body,
                        "priority": 2,
                        "action":   f"Review reminder: {rem.title}",
                    }))
            except Exception as exc:
                log.debug("reminder_proactive_write_error: %s", exc)

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Reminder]:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                return {rid: Reminder.from_dict(d) for rid, d in raw.items()}
            except Exception as exc:
                log.debug("reminder_load_error: %s", exc)
        return {}

    def _save(self) -> None:
        try:
            data = {rid: r.to_dict() for rid, r in self._reminders.items()}
            tmp  = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:
            log.debug("reminder_save_error: %s", exc)
