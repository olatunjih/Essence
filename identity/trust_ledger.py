"""PersonalTrustLedger — learns autonomy preferences from acceptance history.

For each action_class: tracks accept/reject counts → autonomy_level.
autonomy_level: "auto" | "notify" | "require_approval"
Persistence: <workspace>/identity/trust_ledger.json
"""
from __future__ import annotations
import json, time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Literal

AutonomyLevel = Literal["auto", "notify", "require_approval"]
_AUTO_THRESHOLD   = 0.90
_NOTIFY_THRESHOLD = 0.65


@dataclass
class ActionRecord:
    action_class: str
    accepted:     int   = 0
    rejected:     int   = 0
    last_seen:    float = field(default_factory=time.time)

    @property
    def acceptance_rate(self) -> float:
        total = self.accepted + self.rejected
        return self.accepted / total if total else 0.5

    @property
    def autonomy_level(self) -> AutonomyLevel:
        r = self.acceptance_rate
        if r >= _AUTO_THRESHOLD:   return "auto"
        if r >= _NOTIFY_THRESHOLD: return "notify"
        return "require_approval"


class PersonalTrustLedger:
    def __init__(self, workspace: Path) -> None:
        self._path    = workspace / "identity" / "trust_ledger.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, ActionRecord] = self._load()

    def record(self, action_class: str, accepted: bool) -> None:
        r = self._records.setdefault(action_class, ActionRecord(action_class))
        if accepted: r.accepted += 1
        else:        r.rejected += 1
        r.last_seen = time.time()
        self._save()

    def autonomy_for(self, action_class: str) -> AutonomyLevel:
        return self._records.get(
            action_class, ActionRecord(action_class)).autonomy_level

    def report(self) -> list[dict]:
        return [{**asdict(r),
                 "acceptance_rate": r.acceptance_rate,
                 "autonomy":        r.autonomy_level}
                for r in self._records.values()]

    def _save(self) -> None:
        self._path.write_text(
            json.dumps({k: asdict(v) for k, v in self._records.items()}, indent=2))

    def _load(self) -> dict[str, ActionRecord]:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                return {k: ActionRecord(**v) for k, v in raw.items()}
            except Exception:
                pass
        return {}
