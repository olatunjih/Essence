
"""Rubric asset definitions — embedded rubrics for accuracy and docs axes."""
from __future__ import annotations
import hashlib, json
from pathlib import Path


_ACCURACY_RUBRIC = {
    "id":      "accuracy",
    "version": "1.0",
    "description": "Factual accuracy and groundedness rubric.",
    "axes": [
        {"name": "factual_accuracy",  "weight": 0.50,
         "description": "Claims are supported by evidence or cited sources."},
        {"name": "groundedness",      "weight": 0.30,
         "description": "Output is grounded in the task context and reads."},
        {"name": "consistency",       "weight": 0.20,
         "description": "No internal contradictions across output artifacts."},
    ],
    "weights": {"factual_accuracy": 0.50, "groundedness": 0.30, "consistency": 0.20},
    "fallback_on_judge_fail": "fail_closed",
    "hash": "",
}

_DOCS_RUBRIC = {
    "id":      "docs",
    "version": "1.0",
    "description": "Documentation quality rubric.",
    "axes": [
        {"name": "completeness",  "weight": 0.40,
         "description": "All required sections present and non-empty."},
        {"name": "clarity",       "weight": 0.35,
         "description": "Language is clear, precise, and audience-appropriate."},
        {"name": "correctness",   "weight": 0.25,
         "description": "Technical claims and code examples are correct."},
    ],
    "weights": {"completeness": 0.40, "clarity": 0.35, "correctness": 0.25},
    "fallback_on_judge_fail": "escalate_to_human",
    "hash": "",
}


def _compute_hash(d: dict) -> str:
    d2 = {k: v for k, v in d.items() if k != "hash"}
    return hashlib.sha256(
        json.dumps(d2, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def write_rubric_assets(assets_dir: Path) -> None:
    """Write rubric JSON files to assets/rubrics/."""
    rubrics_dir = assets_dir / "rubrics"
    rubrics_dir.mkdir(parents=True, exist_ok=True)
    for rubric in [_ACCURACY_RUBRIC, _DOCS_RUBRIC]:
        r = dict(rubric)
        r["hash"] = _compute_hash(r)
        p = rubrics_dir / f"{r['id']}_rubric.json"
        p.write_text(json.dumps(r, indent=2), encoding="utf-8")
