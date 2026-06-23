
"""APDE seed derivation rules for deterministic LLM calls (Axiom A4)."""
from __future__ import annotations
import hashlib
from essence.apde_types import CallClass

_SEP = b"\x00"

def derive_seed(
    call_class: CallClass,
    task_id: str,
    epoch_id: str,
    rubric_id: str = "",
    rubric_version: str = "",
    base_plan_hash: str = "",
    mutation_event_id: str = "",
) -> int:
    """
    NN-4 / NN-5: Deterministic seed derivation.
    seed = sha256(call_class || task_id || rubric_id_or_empty
                 || rubric_version_or_empty || base_plan_hash_or_empty
                 || mutation_event_id_or_empty || epoch_id)
    Returns the first 8 bytes as an unsigned integer for use as a random seed.
    """
    parts = [
        call_class.value.encode(),
        task_id.encode(),
        rubric_id.encode(),
        rubric_version.encode(),
        base_plan_hash.encode(),
        mutation_event_id.encode(),
        epoch_id.encode(),
    ]
    raw = _SEP.join(parts)
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], "big")


def pool_name_for_class(call_class: CallClass) -> str:
    """Map a CallClass to its manifest pool name."""
    mapping = {
        CallClass.PLAN:   "plan_model",
        CallClass.EXEC:   "exec_model",
        CallClass.VERIFY: "judge_small",
    }
    return mapping[call_class]
