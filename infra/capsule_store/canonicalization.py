
"""APDE canonicalization for deterministic hashing (Axiom A4)."""
from __future__ import annotations
import hashlib, json
from essence.apde_types import IntentCapsule, Task


def _canonical_json(obj: dict) -> bytes:
    """Produce canonical JSON bytes: sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def canonicalize_capsule(capsule: IntentCapsule) -> bytes:
    """Return canonical bytes for an IntentCapsule (for hashing and signing)."""
    d = {
        "id":              capsule.id,
        "raw_prompt":      capsule.raw_prompt,
        "goal":            capsule.goal,
        "success_signals": sorted(capsule.success_signals),
        "artifacts":       sorted(capsule.artifacts),
        "budget":          capsule.budget,
        "constraints":     sorted(capsule.constraints),
        "out_of_scope":    sorted(capsule.out_of_scope),
        "apde_role":       capsule.apde_role,
        "runtime_manifest_id": capsule.runtime_manifest_id,
    }
    return _canonical_json(d)


def canonicalize_task(task: Task) -> bytes:
    """Return canonical bytes for a Task (for plan hashing, Axiom A2)."""
    d = {
        "id":        task.id,
        "capsule_id": task.capsule_id,
        "goal":      task.goal,
        "reads":     sorted(task.reads),
        "writes":    sorted(task.writes),
        "tools":     sorted(task.tools),
        "done_when": task.done_when,
        "risk":      task.risk.value,
    }
    return _canonical_json(d)


def hash_capsule(capsule: IntentCapsule) -> str:
    return hashlib.sha256(canonicalize_capsule(capsule)).hexdigest()


def hash_task(task: Task) -> str:
    return hashlib.sha256(canonicalize_task(task)).hexdigest()
