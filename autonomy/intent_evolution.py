"""IntentEvolutionEngine — tracks how intent shifts across sessions.

Buckets: current (dominant this week), future (next 2 patterns),
         emerging (low-frequency accelerating), abandoned (silent >30d).
Persistence: <workspace>/identity/intent_evolution.json
"""
from __future__ import annotations
import json, time
from pathlib import Path


class IntentEvolutionEngine:
    _ABANDON_DAYS = 30

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "identity" / "intent_evolution.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {"ledger": {}, "current": "", "future": [],
                "emerging": [], "abandoned": []}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._state, indent=2))

    def record(self, intent_type: str) -> None:
        ledger = self._state.setdefault("ledger", {})
        entry  = ledger.setdefault(intent_type, {"count": 0, "last_seen": 0.0})
        entry["count"]    += 1
        entry["last_seen"] = time.time()
        self._recompute()
        self._save()

    def _recompute(self) -> None:
        now    = time.time()
        cutoff = now - self._ABANDON_DAYS * 86400
        ledger = self._state.get("ledger", {})
        sorted_ = sorted(ledger.items(), key=lambda kv: kv[1]["count"], reverse=True)
        active  = [(k, v) for k, v in sorted_ if v["last_seen"] > cutoff]
        dead    = [k for k, v in sorted_ if v["last_seen"] <= cutoff]
        self._state["current"]   = active[0][0] if active else ""
        self._state["future"]    = [k for k, _ in active[1:3]]
        self._state["emerging"]  = [k for k, v in active if v["count"] < 5]
        self._state["abandoned"] = dead

    def summary(self) -> dict:
        return {k: self._state[k]
                for k in ("current", "future", "emerging", "abandoned")}
