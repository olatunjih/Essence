
"""Rubric loading and pinning for LLM-judged verification."""
from __future__ import annotations
import dataclasses as _dc, hashlib, json
from pathlib import Path
from typing import Any


@_dc.dataclass
class Rubric:
    id:          str
    version:     str
    description: str
    axes:        list[dict]
    weights:     dict[str, float]
    fallback_on_judge_fail: str = "fail_closed"
    hash:        str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Rubric":
        return cls(
            id=d["id"], version=d["version"],
            description=d.get("description", ""),
            axes=d.get("axes", []),
            weights=d.get("weights", {}),
            fallback_on_judge_fail=d.get("fallback_on_judge_fail", "fail_closed"),
            hash=d.get("hash", ""),
        )

    def to_dict(self) -> dict:
        return _dc.asdict(self)

    def compute_hash(self) -> str:
        d = self.to_dict()
        d.pop("hash", None)
        return hashlib.sha256(
            json.dumps(d, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def load_rubric(path: Path) -> Rubric:
    """Load a rubric from a JSON file and validate its hash."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rubric = Rubric.from_dict(data)
    computed = rubric.compute_hash()
    if rubric.hash and rubric.hash != computed:
        raise ValueError(
            f"Rubric {rubric.id} hash mismatch: "
            f"stored={rubric.hash[:8]} computed={computed[:8]}")
    return rubric


class RubricRegistry:
    """Registry of loaded and pinned rubrics."""

    def __init__(self) -> None:
        self._rubrics: dict[str, Rubric] = {}

    def register(self, rubric: Rubric) -> None:
        self._rubrics[rubric.id] = rubric

    def load_from_directory(self, assets_dir: Path) -> None:
        rubrics_dir = assets_dir / "rubrics"
        if not rubrics_dir.exists():
            return
        for p in rubrics_dir.glob("*.json"):
            try:
                r = load_rubric(p)
                self.register(r)
            except Exception as e:
                raise RuntimeError(f"Failed to load rubric {p}: {e}") from e

    def get(self, rubric_id: str) -> Rubric:
        if rubric_id not in self._rubrics:
            raise KeyError(f"Rubric '{rubric_id}' not registered")
        return self._rubrics[rubric_id]

    def all_ids(self) -> list[str]:
        return list(self._rubrics.keys())
