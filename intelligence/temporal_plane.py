"""TemporalCognitionPlane — multi-horizon goal registry and conflict detection.

Horizons: TODAY | THIS_WEEK | THIS_MONTH | THIS_YEAR | FIVE_YEAR
Persistence: <workspace>/identity/temporal_goals.json
"""
from __future__ import annotations
import dataclasses, json, time
from pathlib import Path

HORIZONS = ("TODAY", "THIS_WEEK", "THIS_MONTH", "THIS_YEAR", "FIVE_YEAR")


@dataclasses.dataclass
class TemporalGoal:
    horizon:     str
    description: str
    set_at:      float = dataclasses.field(default_factory=time.time)
    completed:   bool  = False


@dataclasses.dataclass
class ConflictReport:
    has_conflict:      bool
    conflicting_goals: list[str]
    advice:            str


class TemporalCognitionPlane:
    def __init__(self, workspace: Path) -> None:
        self._path  = workspace / "identity" / "temporal_goals.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._goals: dict[str, TemporalGoal] = self._load()

    def set_goal(self, horizon: str, description: str) -> TemporalGoal:
        if horizon not in HORIZONS:
            raise ValueError(f"Unknown horizon: {horizon}")
        goal = TemporalGoal(horizon=horizon, description=description)
        self._goals[horizon] = goal
        self._save()
        return goal

    def get_goal(self, horizon: str) -> TemporalGoal | None:
        return self._goals.get(horizon)

    def all_goals(self) -> list[dict]:
        return [dataclasses.asdict(g)
                for g in self._goals.values() if not g.completed]

    def check_conflict(self, proposed_action: str) -> ConflictReport:
        conflicts: list[str] = []
        action_lc = proposed_action.lower()
        for horizon, goal in self._goals.items():
            if goal.completed:
                continue
            if any(kw in action_lc for kw in ("skip", "ignore", "abandon", "cancel")):
                if any(kw in goal.description.lower()
                       for kw in ("health", "learn", "build", "launch", "study")):
                    conflicts.append(f"[{horizon}] {goal.description}")
        if not conflicts:
            return ConflictReport(False, [], "No temporal conflict detected.")
        return ConflictReport(
            True, conflicts,
            f"This action may undermine {len(conflicts)} long-term goal(s): "
            + "; ".join(conflicts)
        )

    def _save(self) -> None:
        self._path.write_text(
            json.dumps({k: dataclasses.asdict(v)
                        for k, v in self._goals.items()}, indent=2))

    def _load(self) -> dict[str, TemporalGoal]:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                return {k: TemporalGoal(**v) for k, v in raw.items()}
            except Exception:
                pass
        return {}
