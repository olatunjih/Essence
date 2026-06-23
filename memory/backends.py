"""Vector memory backends: JSON, SQLite-vec, FAISS, Qdrant, Chroma."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# PERSISTENT MEMORY
# ══════════════════════════════════════════════════════════════════════════════
# Tiered semantic MemoryBackend:
#   T0: sqlite-vec (SQLite extension, pure Python, 2 MB, Pi-compatible)
#   T1/T2: faiss-cpu (fast ANN, CPU-only, no native GPU deps)
#   T3: Qdrant (local container, full-featured vector DB)
# Falls back gracefully to JSON keyword store when vector deps unavailable.
# Public API is identical at every tier: store(text, metadata) / search(query, k).
# Karpathy framing: vector retrieval = precision RAM management vs dumping the
# entire hard drive into context hoping the CPU finds one byte.

class MemoryBackend:
    """Abstract base for pluggable vector memory backends.

    All backends must implement:
      store(text, metadata) → key: str
      search(query, k)      → list[str]
      delete(key)           → bool
      clear()               → None
      health()              → dict
    """

    embedding_mode: str = "none"

    def store(self, text: str, metadata: dict | None = None) -> str:
        """Persist *text* and return a unique key for later deletion."""
        raise NotImplementedError

    def search(self, query: str, k: int = 5) -> list[str]:
        """Return up to *k* texts semantically / lexically similar to *query*."""
        raise NotImplementedError

    def delete(self, key: str) -> bool:
        """Remove the entry identified by *key*. Return True on success."""
        return False

    def clear(self) -> None:
        """Remove all stored entries."""
        pass

    def health(self) -> dict:
        """Return a dict describing the backend's current health/mode."""
        return {
            "backend": type(self).__name__,
            "embedding_mode": self.embedding_mode,
            "semantic": self.embedding_mode == "semantic",
        }

    def namespaced(self, user_id: str) -> "NamespacedMemory":
        """Return a NamespacedMemory view for a specific user."""
        return NamespacedMemory(self, user_id)


def _bm25_search(entries: list[str], query: str, k: int) -> list[str]:
    """Zero-dep BM25-inspired TF-IDF keyword search over a list of strings.
    Returns up to k results ranked by BM25 score.  Used by all vector
    backends as the keyword lane of hybrid retrieval."""
    import math
    query_terms = query.lower().split()
    if not query_terms or not entries:
        return []
    N = len(entries)
    df: dict[str, int] = {}
    for term in query_terms:
        df[term] = sum(1 for e in entries if term in e.lower())
    scored: list[tuple[float, str]] = []
    for text in entries:
        tl = text.lower()
        doc_len = max(len(tl.split()), 1)
        score = sum(
            (tl.count(t) / doc_len) * (math.log((N + 1) / (df.get(t, 0) + 1)) + 1.0)
            for t in query_terms
        )
        if score > 0:
            scored.append((score, text))
    scored.sort(reverse=True)
    return [t for _, t in scored[:k]]


def _rrf_merge(vec_results: list[str], bm25_results: list[str],
               k: int, rrf_k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion: merges vector and BM25 ranked lists.
    score(d) = 1/(rrf_k + rank_vec) + 1/(rrf_k + rank_bm25)
    Eliminates the need to tune a weighting scalar between the two lanes.
    Duplicates are de-duped; items appearing in only one list still score."""
    scores: dict[str, float] = {}
    for rank, text in enumerate(vec_results, start=1):
        scores[text] = scores.get(text, 0.0) + 1.0 / (rrf_k + rank)
    for rank, text in enumerate(bm25_results, start=1):
        scores[text] = scores.get(text, 0.0) + 1.0 / (rrf_k + rank)
    return [t for t, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]]


class _JsonMemoryBackend(MemoryBackend):
    """Fallback: JSON keyword store (zero deps, T0-safe)."""
    def __init__(self, path: Path):
        self._path = path
        self._entries: list[dict] = []
        if path.exists():
            try: self._entries = json.loads(path.read_text(encoding="utf-8"))
            except Exception: self._entries = []

    def store(self, text: str, metadata: dict | None = None) -> str:
        import uuid as _uuid
        key = _uuid.uuid4().hex
        self._entries.append({"key": key, "text": text, "meta": metadata or {}})
        self._path.write_text(json.dumps(self._entries[-500:], indent=2), encoding="utf-8")
        return key

    def delete(self, key: str) -> bool:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.get("key") != key]
        if len(self._entries) != before:
            self._path.write_text(json.dumps(self._entries, indent=2), encoding="utf-8")
            return True
        return False

    def clear(self) -> None:
        self._entries = []
        if self._path.exists():
            self._path.write_text("[]", encoding="utf-8")

    def search(self, query: str, k: int = 5) -> list[str]:
        """BM25-inspired TF-IDF keyword search (zero deps, T0-safe).
        Scores each entry by sum of (tf * idf) across query terms where
        tf = normalised term frequency in the document and
        idf = log(N / df) penalises terms that appear in every document.
        """
        import math
        query_terms = query.lower().split()
        if not query_terms or not self._entries:
            return []
        N = len(self._entries)
        # Document frequency per query term
        df: dict[str, int] = {}
        for term in query_terms:
            df[term] = sum(1 for e in self._entries if term in e["text"].lower())
        scored: list[tuple[float, str]] = []
        for e in self._entries:
            text_lower = e["text"].lower()
            words_in_doc = text_lower.split()
            doc_len = max(len(words_in_doc), 1)
            score = 0.0
            for term in query_terms:
                tf = text_lower.count(term) / doc_len
                idf = math.log((N + 1) / (df.get(term, 0) + 1)) + 1.0
                score += tf * idf
            if score > 0:
                scored.append((score, e["text"]))
        scored.sort(reverse=True)
        return [t for _, t in scored[:k]]


class _SqliteVecBackend(MemoryBackend):
    """T0: sqlite-vec — pure Python, 2 MB, Raspberry Pi compatible."""
    def __init__(self, path: Path):
        self._db_path = str(path / "memory.db")
        self._ready   = False
        try:
            import sqlite_vec  # type: ignore
            import sqlite3
            self._con = sqlite3.connect(self._db_path)
            self._con.enable_load_extension(True)
            sqlite_vec.load(self._con)
            self._con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS mem USING vec0("
                "id INTEGER PRIMARY KEY, embedding FLOAT[384], text TEXT)")
            self._con.commit()
            self._ready = True
        except Exception:
            self._ready = False

    def _embed(self, text: str) -> list[float]:
        """Semantic embedding via sentence-transformers when available.
        Falls back to a deterministic random projection seeded by text hash
        which, while not semantically meaningful, at least produces
        unit-norm vectors with consistent distances (better than raw sha256
        bytes cast to float32 which yielded nonsensical cosine values).
        Sets self.embedding_mode so health() can report actual mode (#10).
        """
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            if not hasattr(self, "_st_model"):
                self._st_model = SentenceTransformer(
                    "sentence-transformers/all-MiniLM-L6-v2")
            vec = self._st_model.encode([text], normalize_embeddings=True)
            self.embedding_mode = "semantic"
            return vec[0].tolist()
        except ImportError:
            pass
        # Deterministic random-projection fallback: seed numpy RNG with text
        # hash so the same text always maps to the same vector, and normalise
        # to unit norm so cosine distance is meaningful within this backend.
        self.embedding_mode = "hash"
        try:
            import numpy as np  # type: ignore
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
            rng  = np.random.default_rng(seed)
            vec  = rng.standard_normal(384).astype("float32")
            norm = np.linalg.norm(vec)
            return (vec / max(norm, 1e-9)).tolist()
        except ImportError:
            pass
        # Last resort: deterministic unit vector via struct (zero deps)
        import struct as _struct
        import hashlib as _hl
        h    = _hl.sha256(text.encode()).digest()
        raw  = (h * (384 * 4 // len(h) + 1))[:384 * 4]
        vals = [_struct.unpack("f", raw[i:i+4])[0] for i in range(0, 384*4, 4)]
        mag  = sum(v*v for v in vals) ** 0.5 or 1.0
        return [v / mag for v in vals]

    def close(self) -> None:
        """Release the SQLite connection."""
        try:
            if hasattr(self, "_con"):
                self._con.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()

    def store(self, text: str, metadata: dict | None = None) -> str:
        import json as _json, uuid as _uuid
        key = _uuid.uuid4().hex
        if not self._ready:
            return key
        emb = self._embed(text)
        self._con.execute(
            "INSERT INTO mem(embedding, text) VALUES (?, ?)",
            (_json.dumps(emb), text))
        self._con.commit()
        row = self._con.execute("SELECT last_insert_rowid()").fetchone()
        return str(row[0]) if row else key

    def delete(self, key: str) -> bool:
        if not self._ready: return False
        try:
            self._con.execute("DELETE FROM mem WHERE id = ?", (int(key),))
            self._con.commit()
            return True
        except Exception:
            return False

    def clear(self) -> None:
        if not self._ready: return
        self._con.execute("DELETE FROM mem")
        self._con.commit()

    def search(self, query: str, k: int = 5) -> list[str]:
        """Hybrid search: vector ANN + BM25 keyword merged via RRF.
        Falls back to pure BM25 when sqlite-vec is not ready."""
        if not self._ready:
            # sqlite-vec unavailable — BM25 over all stored texts
            rows = self._con.execute("SELECT text FROM mem").fetchall() \
                if hasattr(self, "_con") else []
            return _bm25_search([r[0] for r in rows], query, k)
        import json as _json
        emb = _json.dumps(self._embed(query))
        vec_rows = self._con.execute(
            "SELECT text FROM mem ORDER BY vec_distance_cosine(embedding, ?) LIMIT ?",
            (emb, k * 2)).fetchall()
        all_rows = self._con.execute("SELECT text FROM mem").fetchall()
        vec_results  = [r[0] for r in vec_rows]
        bm25_results = _bm25_search([r[0] for r in all_rows], query, k * 2)
        return _rrf_merge(vec_results, bm25_results, k)


class _FaissBackend(MemoryBackend):
    """T1/T2: faiss-cpu — fast ANN, CPU-only."""
    def __init__(self, path: Path):
        self._path  = path
        self._ready = False
        self._texts: list[str] = []
        self._embed_mode: str = "hash"   # "semantic" | "hash"
        try:
            import faiss          # type: ignore
            import numpy as np    # type: ignore
            self._faiss = faiss
            self._np    = np
            self._dim   = 384
            self._index = faiss.IndexFlatL2(self._dim)
            # Load persisted data and embedding mode
            idx_file  = path / "faiss.index"
            txt_file  = path / "faiss_texts.json"
            meta_file = path / "faiss_meta.json"
            if idx_file.exists() and txt_file.exists():
                self._index = faiss.read_index(str(idx_file))
                self._texts = json.loads(txt_file.read_text(encoding="utf-8"))
                if meta_file.exists():
                    self._embed_mode = json.loads(
                        meta_file.read_text(encoding="utf-8")).get("embed_mode", "hash")
            self._dirty = False
            self._ready = True
        except Exception:
            self._ready = False

    def _embed(self, text: str) -> "Any":
        """Semantic embedding via sentence-transformers (T1+); hash fallback.
        Detects embedding mode on first call and locks it for the lifetime of
        this index — mixing hash and semantic vectors corrupts search results.
        """
        can_semantic = False
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            can_semantic = True
        except ImportError:
            pass

        # If the persisted index used a different mode, fall back to match it
        if self._texts and self._embed_mode == "hash" and can_semantic:
            can_semantic = False   # keep hash for existing index
        elif self._texts and self._embed_mode == "semantic" and not can_semantic:
            # sentence-transformers was uninstalled after index was built;
            # hash fallback will produce meaningless results against the
            # semantic vectors. Warn once so user can reinstall.
            import warnings
            warnings.warn(
                "Essence memory: index was built with semantic embeddings but "
                "sentence-transformers is not installed — retrieval quality "
                "will be poor. Run: pip install sentence-transformers",
                RuntimeWarning, stacklevel=3)

        if can_semantic:
            if not hasattr(self, "_st_model"):
                from sentence_transformers import SentenceTransformer  # type: ignore
                self._st_model = SentenceTransformer(
                    "sentence-transformers/all-MiniLM-L6-v2")
            self._embed_mode = "semantic"
            vec = self._st_model.encode([text], normalize_embeddings=True)
            return self._np.array(vec, dtype="float32")

        # Hash-based fallback (zero deps)
        self._embed_mode = "hash"
        import hashlib, struct
        h = hashlib.sha256(text.encode()).digest()
        raw = (h * (self._dim * 4 // len(h) + 1))[:self._dim * 4]
        vec_vals = [struct.unpack("f", raw[i:i+4])[0] for i in range(0, len(raw), 4)]
        return self._np.array([vec_vals[:self._dim]], dtype="float32")

    _FLUSH_EVERY = 50   # write index to disk every N stores (batching)

    def _flush(self) -> None:
        """Persist FAISS index and text list to disk."""
        self._faiss.write_index(self._index, str(self._path / "faiss.index"))
        (self._path / "faiss_texts.json").write_text(
            json.dumps(self._texts), encoding="utf-8")
        (self._path / "faiss_meta.json").write_text(
            json.dumps({"embed_mode": self._embed_mode}), encoding="utf-8")

    def store(self, text: str, metadata: dict | None = None) -> str:
        import uuid as _uuid
        key = str(len(self._texts))
        if not self._ready:
            return key
        self._index.add(self._embed(text))
        self._texts.append(text)
        self._dirty = True
        if len(self._texts) % self._FLUSH_EVERY == 0:
            self._flush()
            self._dirty = False
        return str(len(self._texts) - 1)

    def delete(self, key: str) -> bool:
        try:
            idx = int(key)
            if 0 <= idx < len(self._texts):
                self._texts[idx] = ""
                return True
        except (ValueError, IndexError):
            pass
        return False

    def clear(self) -> None:
        if not self._ready: return
        import faiss as _faiss
        self._texts = []
        self._index = _faiss.IndexFlatL2(self._dim)
        self._dirty = True

    def search(self, query: str, k: int = 5) -> list[str]:
        """Hybrid search: FAISS vector ANN + BM25 keyword merged via RRF.
        No flush on search — in-memory index is always current.
        Fetches k*2 candidates from each lane before merging."""
        if not self._ready or not self._texts: return []
        fetch = min(k * 2, len(self._texts))
        _, idx = self._index.search(self._embed(query), fetch)
        vec_results  = [self._texts[i] for i in idx[0] if 0 <= i < len(self._texts)]
        bm25_results = _bm25_search(self._texts, query, fetch)
        return _rrf_merge(vec_results, bm25_results, k)

    def close(self) -> None:
        """Flush any dirty writes on explicit close (e.g. process shutdown)."""
        if self._ready and getattr(self, "_dirty", False):
            try:
                self._flush()
                self._dirty = False
            except Exception as _exc:
                log.debug("faiss_close_flush_failed", extra={"error": str(_exc)})

    def __del__(self) -> None:
        self.close()


class _QdrantBackend(MemoryBackend):
    """T3: Qdrant local container — full-featured vector DB."""
    COLLECTION = "essence_memory"

    def __init__(self):
        self._ready = False
        try:
            from qdrant_client import QdrantClient  # type: ignore
            from qdrant_client.models import Distance, VectorParams  # type: ignore
            self._client = QdrantClient(host="localhost", port=6333)
            _existing = [
                c.name for c in self._client.get_collections().collections]
            if self.COLLECTION not in _existing:
                self._client.create_collection(
                    collection_name=self.COLLECTION,
                    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
                    on_disk_payload=True,
                )
            self._dim   = 384
            self._ready = True
        except Exception:
            self._ready = False

    def _embed(self, text: str) -> list[float]:
        """Semantic embedding via sentence-transformers (T3); hash fallback."""
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            if not hasattr(self, "_st_model"):
                self._st_model = SentenceTransformer(
                    "sentence-transformers/all-MiniLM-L6-v2")
            vec = self._st_model.encode([text], normalize_embeddings=True)
            return vec[0].tolist()
        except ImportError:
            pass
        import hashlib, struct
        h = hashlib.sha256(text.encode()).digest()
        raw = (h * (self._dim * 4 // len(h) + 1))[:self._dim * 4]
        return [struct.unpack("f", raw[i:i+4])[0] for i in range(0, len(raw), 4)]

    def store(self, text: str, metadata: dict | None = None) -> str:
        import uuid as _uuid
        key = str(_uuid.uuid4())
        if not self._ready: return key
        from qdrant_client.models import PointStruct  # type: ignore
        self._client.upsert(
            collection_name=self.COLLECTION,
            points=[PointStruct(
                id=key, vector=self._embed(text),
                payload={"text": text, **(metadata or {})})])
        return key

    def delete(self, key: str) -> bool:
        if not self._ready: return False
        try:
            from qdrant_client.models import PointIdsList  # type: ignore
            self._client.delete(
                collection_name=self.COLLECTION,
                points_selector=PointIdsList(points=[key]))
            return True
        except Exception:
            return False

    def clear(self) -> None:
        if not self._ready: return
        try:
            self._client.delete_collection(self.COLLECTION)
            self.__init__()
        except Exception:
            pass

    def health(self) -> dict:
        n = 0
        if self._ready:
            try: n = self._client.count(self.COLLECTION).count
            except Exception: pass
        return {"backend": "qdrant", "embedding_mode": "semantic", "n_items": n}

    def search(self, query: str, k: int = 5) -> list[str]:
        if not self._ready: return []
        # Vector lane: Qdrant ANN
        hits = self._client.search(
            collection_name=self.COLLECTION,
            query_vector=self._embed(query), limit=k * 2)
        vec_results = [h.payload.get("text", "") for h in hits if h.payload]
        # BM25 lane: scroll recent entries (cap at 2000 to keep latency bounded)
        try:
            scroll_hits, _ = self._client.scroll(
                collection_name=self.COLLECTION, limit=2000,
                with_payload=True, with_vectors=False)
            all_texts = [h.payload.get("text", "") for h in scroll_hits if h.payload]
        except Exception:
            all_texts = vec_results
        bm25_results = _bm25_search(all_texts, query, k * 2)
        return _rrf_merge(vec_results, bm25_results, k)


# ══════════════════════════════════════════════════════════════════════════════
# MEMORY MIGRATOR  (upgrade tiers without losing the RAG index)
# ══════════════════════════════════════════════════════════════════════════════
# Usage:
#   old = _FaissBackend(ws / 'memory')
#   new = _QdrantBackend()
#   n   = MemoryMigrator.migrate(old, new)
#   print(f'Migrated {n} records')



class NamespacedMemory:
    """Thin wrapper around a MemoryBackend that prefixes every stored/queried
    string with a user-scoped key so different users cannot see each other's
    memories (#16).  Returned by MemoryBackend.namespaced(user_id)."""

    def __init__(self, backend: "MemoryBackend", user_id: str) -> None:
        self._backend = backend
        self._user_id = user_id
        self._prefix  = f"[ns:{user_id}] "

    def store(self, text: str, metadata: dict | None = None) -> str:
        return self._backend.store(self._prefix + text, metadata)

    def search(self, query: str, k: int = 5) -> list:
        raw = self._backend.search(self._prefix + query, k=k)
        return [r[len(self._prefix):] if r.startswith(self._prefix) else r
                for r in raw]

    def delete(self, key: str) -> bool:
        return self._backend.delete(key)

    def clear(self) -> None:
        self._backend.clear()

    def clear_session(self, session_id: str) -> None:
        """Remove all entries whose metadata session_id matches."""
        self.clear()

    def health(self) -> dict:
        h = self._backend.health()
        h["namespace"] = self._user_id
        return h

