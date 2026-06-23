""" — accurate token counting (tiktoken)."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# ACCURATE TOKEN COUNTING  — tiktoken with char fallback
# ══════════════════════════════════════════════════════════════════════════════
# Replaces the 4-chars-per-token heuristic with proper BPE counting.
# Used by CostTracker, ContextBudgetManager, and the budget guard.
# Falls back to len(text)//4 when tiktoken is not installed.
#
# ENV:  Essence_TIKTOKEN_MODEL=cl100k_base   encoding name (default: cl100k_base)

_TIKTOKEN_MODEL = os.environ.get("Essence_TIKTOKEN_MODEL", "cl100k_base")
_tiktoken_enc: Any = None
_tiktoken_lock = threading.Lock()

try:
    import tiktoken as _tiktoken_mod  # type: ignore
    _TIKTOKEN = True
except ImportError:
    _tiktoken_mod = None  # type: ignore
    _TIKTOKEN = False


def count_tokens(text: str) -> int:
    """
    Count tokens in text accurately when tiktoken is available.
    Falls back to len(text) // 4 (conservative) on ImportError.
    Thread-safe: encoder is loaded once and cached.
    """
    global _tiktoken_enc
    if not _TIKTOKEN:
        return max(1, len(text) // 4)
    with _tiktoken_lock:
        if _tiktoken_enc is None:
            try:
                _tiktoken_enc = _tiktoken_mod.get_encoding(_TIKTOKEN_MODEL)
            except Exception:
                return max(1, len(text) // 4)
    try:
        return len(_tiktoken_enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def count_messages_tokens(messages: list[dict]) -> int:
    """Count tokens across a messages list (system + history)."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += count_tokens(part.get("text", ""))
        # Per-message overhead: role + delimiters ≈ 4 tokens
        total += 4
    return total + 2   # conversation-level priming


# ══════════════════════════════════════════════════════════════════════════════
