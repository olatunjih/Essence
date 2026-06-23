"""PersonalTwin — continuously updated structured belief model of the user.

Axes: goals, values, preferences, habits, skills, career, health,
      learning, financial, relationships, personality, decision_patterns.

Each entry is stored as a Belief with confidence, evidence count, source
tracking, and per-entry timestamps.  Confidence is updated Bayesian-style:
confirmations raise it, contradictions lower it and record the conflict.

Persistence: <workspace>/identity/twin.json
"""
from __future__ import annotations
import dataclasses
import json
import time
from pathlib import Path
from typing import Any

_AXES = [
    "goals", "values", "preferences", "habits", "skills",
    "career", "health", "learning", "financial", "relationships",
    "personality", "decision_patterns",
]

_CONTEXT_ITEMS_PER_AXIS   = 6
_CONTEXT_CONFIDENCE_FLOOR = 0.3   # beliefs below this are omitted from context_block
_CONFIDENCE_DELTA         = 0.10  # per-confirmation step


@dataclasses.dataclass
class Belief:
    """A single piece of knowledge about the user."""
    value:           Any
    confidence:      float = 0.5          # 0.0–1.0
    evidence_count:  int   = 1
    source:          str   = "asserted"   # "observed" | "inferred" | "asserted"
    first_seen:      float = dataclasses.field(default_factory=time.time)
    last_confirmed:  float = dataclasses.field(default_factory=time.time)
    superseded_by:   str | None = None
    contradictions:  list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "value":          self.value,
            "confidence":     self.confidence,
            "evidence_count": self.evidence_count,
            "source":         self.source,
            "first_seen":     self.first_seen,
            "last_confirmed": self.last_confirmed,
            "superseded_by":  self.superseded_by,
            "contradictions": self.contradictions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Belief":
        return cls(
            value           = d.get("value"),
            confidence      = float(d.get("confidence", 0.5)),
            evidence_count  = int(d.get("evidence_count", 1)),
            source          = d.get("source", "asserted"),
            first_seen      = float(d.get("first_seen", time.time())),
            last_confirmed  = float(d.get("last_confirmed", time.time())),
            superseded_by   = d.get("superseded_by"),
            contradictions  = d.get("contradictions", []),
        )

    @classmethod
    def from_legacy(cls, value: Any) -> "Belief":
        """Upgrade a plain value read from an older twin.json."""
        return cls(value=value, source="asserted", confidence=0.5)


class PersonalTwin:
    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "identity" / "twin.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._beliefs: dict[str, dict[str, Belief]] = self._load()

    # ── persistence ─────────────────────────────────────────────────────────

    def _load(self) -> dict[str, dict[str, Belief]]:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                out: dict[str, dict[str, Belief]] = {}
                for axis, entries in raw.items():
                    if axis.startswith("_"):
                        continue
                    if not isinstance(entries, dict):
                        continue
                    out[axis] = {}
                    for key, val in entries.items():
                        if isinstance(val, dict) and "confidence" in val:
                            out[axis][key] = Belief.from_dict(val)
                        else:
                            out[axis][key] = Belief.from_legacy(val)
                return out
            except Exception:
                pass
        return {ax: {} for ax in _AXES}

    def _save(self) -> None:
        serialisable: dict = {}
        for axis, entries in self._beliefs.items():
            serialisable[axis] = {k: b.to_dict() for k, b in entries.items()}
        self._path.write_text(json.dumps(serialisable, indent=2), encoding="utf-8")

    # ── public API ──────────────────────────────────────────────────────────

    def update(self, axis: str, key: str, value: Any,
               source: str = "asserted",
               confidence_delta: float = _CONFIDENCE_DELTA) -> None:
        """
        Record a belief.  If the same key already exists:
          - Same value  → confirmation: raise confidence by confidence_delta.
          - New value   → contradiction: lower old confidence, record conflict,
                          store new belief with moderate starting confidence.
        """
        axis_beliefs = self._beliefs.setdefault(axis, {})
        existing = axis_beliefs.get(key)

        if existing is None:
            starting = 0.6 if source == "observed" else 0.5
            axis_beliefs[key] = Belief(
                value=value, source=source, confidence=starting)
        elif existing.value == value:
            # Confirmation
            new_conf = min(1.0, existing.confidence + confidence_delta)
            axis_beliefs[key] = dataclasses.replace(
                existing,
                confidence     = new_conf,
                evidence_count = existing.evidence_count + 1,
                last_confirmed = time.time(),
            )
        else:
            # Contradiction — demote old belief, record conflict
            old_key_str = f"{key}={existing.value}"
            demoted = dataclasses.replace(
                existing,
                confidence    = max(0.0, existing.confidence - confidence_delta * 2),
                contradictions = existing.contradictions + [str(value)],
                superseded_by  = str(value),
            )
            axis_beliefs[key] = demoted
            # Store new belief alongside demoted one (overwrite)
            new_starting = 0.55 if source == "observed" else 0.45
            axis_beliefs[key] = Belief(
                value=value, source=source, confidence=new_starting,
                contradictions=[old_key_str])
        self._save()

    def get(self, axis: str, key: str, default: Any = None) -> Any:
        belief = self._beliefs.get(axis, {}).get(key)
        if belief is None:
            return default
        return belief.value

    def get_belief(self, axis: str, key: str) -> Belief | None:
        return self._beliefs.get(axis, {}).get(key)

    def all_beliefs(self, axis: str) -> dict[str, Belief]:
        return dict(self._beliefs.get(axis, {}))

    def context_block(self) -> str:
        """
        Compact string injected into LLM system prompts.

        Only includes beliefs above _CONTEXT_CONFIDENCE_FLOOR, ranked
        by confidence descending, capped at _CONTEXT_ITEMS_PER_AXIS per axis.
        """
        lines = ["[USER PROFILE]"]
        for ax in _AXES:
            entries = self._beliefs.get(ax, {})
            if not entries:
                continue
            ranked = sorted(
                ((k, b) for k, b in entries.items()
                 if b.confidence >= _CONTEXT_CONFIDENCE_FLOOR),
                key=lambda x: x[1].confidence,
                reverse=True,
            )[: _CONTEXT_ITEMS_PER_AXIS]
            if ranked:
                kv = ", ".join(
                    f"{k}={b.value}({b.confidence:.1f})"
                    for k, b in ranked
                )
                lines.append(f"  {ax}: {kv}")
        return "\n".join(lines)

    def purge_low_confidence(self, threshold: float = 0.15) -> int:
        """Remove beliefs that have fallen below *threshold*. Returns count removed."""
        removed = 0
        for axis in list(self._beliefs):
            before = len(self._beliefs[axis])
            self._beliefs[axis] = {
                k: b for k, b in self._beliefs[axis].items()
                if b.confidence >= threshold
            }
            removed += before - len(self._beliefs[axis])
        if removed:
            self._save()
        return removed
