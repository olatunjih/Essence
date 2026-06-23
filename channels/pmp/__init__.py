
"""Prompt Mutation Protocol (PMP) — 5-phase mutation pipeline."""
from essence.channels.pmp.intent_diff import IntentDiff      # noqa: F401
from essence.channels.pmp.dispositions import DispositionMatrix  # noqa: F401
from essence.channels.pmp.pipeline import PMPPipeline         # noqa: F401
from essence.channels.pmp.scratch import ScratchNamespace     # noqa: F401
from essence.channels.pmp.disposition_summary import DispositionSummary  # noqa: F401
__all__ = [
    "IntentDiff", "DispositionMatrix", "PMPPipeline",
    "ScratchNamespace", "DispositionSummary",
]
