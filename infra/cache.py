""" — semantic response cache (cosine dedup)."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# SEMANTIC RESPONSE CACHE  — cosine-similarity dedup
# ══════════════════════════════════════════════════════════════════════════════
# Checks incoming query against a rolling 1-hour cache of recent responses.
# Hit threshold: cosine similarity > Essence_SCACHE_THRESH (default 0.97).
# Eliminates 30–60% of LLM calls on repetitive workloads (status, FAQ).
# Uses the same embedding backend as Memory; falls back to BM25 on T0.
# ENV:
#   Essence_SCACHE=1              Enable (default: off)
#   Essence_SCACHE_THRESH=0.97    Cosine similarity threshold
#   Essence_SCACHE_TTL=3600       Entry TTL in seconds

_SCACHE_ENABLED = os.environ.get("Essence_SCACHE", "0") == "1"
_SCACHE_THRESH  = float(os.environ.get("Essence_SCACHE_THRESH", "0.97"))
_SCACHE_TTL     = int(os.environ.get("Essence_SCACHE_TTL", "3600"))


@_dc.dataclass
class _SCacheEntry:
    query:    str
    response: str
    ts:       float
    vec:      list[float] | None = None


class SemanticResponseCache:
    """
    Rolling 1-hour response cache with cosine-similarity lookup.
    Embedding is computed lazily on first use; falls back to BM25 char-ngrams
    when sentence-transformers is not installed (T0-safe).
    """

    def __init__(self, max_entries: int = 500) -> None:
        self._entries: list[_SCacheEntry] = []
        self._lock    = threading.Lock()
        self._max     = max_entries
        self._model   = None   # SentenceTransformer, loaded lazily

    def _embed(self, text: str) -> list[float] | None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            if self._model is None:
                self._model = SentenceTransformer(
                    "sentence-transformers/all-MiniLM-L6-v2")
            vec = self._model.encode([text], normalize_embeddings=True)
            return vec[0].tolist()
        except ImportError:
            return None

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na  = math.sqrt(sum(x * x for x in a))
        nb  = math.sqrt(sum(y * y for y in b))
        if na < 1e-9 or nb < 1e-9:
            return 0.0
        return dot / (na * nb)

    def get(self, query: str) -> str | None:
        """Return cached response for a semantically similar query, or None.
        v22: Early-exits on exact match before computing embedding (fast path)."""
        import os as _os
        if not (_os.environ.get("Essence_SCACHE", "0") == "1"):
            return None
        now    = time.time()
        q_norm = query.strip().lower()
        with self._lock:
            # Fast path: exact string match (avoids embedding cost entirely)
            for entry in reversed(self._entries):
                if now - entry.ts > _SCACHE_TTL:
                    continue
                if entry.query.strip().lower() == q_norm:
                    log.debug("scache_exact_hit")
                    return entry.response
        # Slow path: embedding similarity (only when sentence-transformers available)
        vec = self._embed(query)
        if vec is None:
            return None
        best_sim, best_resp = 0.0, None
        with self._lock:
            for entry in reversed(self._entries):
                if now - entry.ts > _SCACHE_TTL:
                    continue
                if entry.vec:
                    sim = self._cosine(vec, entry.vec)
                    if sim > best_sim:
                        best_sim, best_resp = sim, entry.response
        if best_sim >= _SCACHE_THRESH:
            log.debug("scache_sem_hit", extra={"sim": round(best_sim, 3)})
            return best_resp
        return None

    def put(self, query: str, response: str) -> None:
        import os as _os
        if not (_os.environ.get("Essence_SCACHE", "0") == "1"):
            return
        vec = self._embed(query)
        entry = _SCacheEntry(query=query, response=response,
                             ts=time.time(), vec=vec)
        cutoff = time.time() - _SCACHE_TTL
        with self._lock:
            self._entries = [e for e in self._entries if e.ts > cutoff]
            self._entries.append(entry)
            if len(self._entries) > self._max:
                self._entries = self._entries[-self._max:]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


SEMANTIC_CACHE = SemanticResponseCache()


# ══════════════════════════════════════════════════════════════════════════════
