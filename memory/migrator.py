"""MemoryMigrator: upgrade memory tiers without losing RAG index."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

from essence.memory.memory import Memory  # noqa: F401
from essence.memory.memory import _make_memory_backend  # noqa: F401  [moved from this file: real source bug]
from essence.memory.backends import MemoryBackend  # noqa: F401
from essence.memory.backends import (  # noqa: F401  [real source bug: used in _extract() without import]
    _JsonMemoryBackend, _FaissBackend, _SqliteVecBackend, _QdrantBackend,
)
from essence.memory.episodic import EpisodicStore  # noqa: F401
from essence.agents.specialist import AgentRole  # noqa: F401
from essence.infra.schema import SCHEMA_REGISTRY  # noqa: F401

class MemoryMigrator:
    """
    Export all vectors from one MemoryBackend, re-embed and import into
    another.  Allows hardware tier upgrades (T1→T3, FAISS→Qdrant) without
    discarding the accumulated RAG index.
    """

    @staticmethod
    def _extract(source: MemoryBackend) -> list[tuple[str, dict]]:
        """Pull all (text, metadata) pairs from a backend."""
        records: list[tuple[str, dict]] = []
        if isinstance(source, _JsonMemoryBackend):
            records = [(e['text'], e.get('meta', {}))
                       for e in source._entries]
        elif isinstance(source, _FaissBackend) and source._ready:
            records = [(t, {}) for t in source._texts]
        elif isinstance(source, _SqliteVecBackend) and source._ready:
            try:
                rows = source._con.execute(
                    'SELECT text FROM mem').fetchall()
                records = [(r[0], {}) for r in rows]
            except Exception as _e:
                log.debug("memory_migrate_sqlite_read_error", extra={"error": str(_e)[:120]})
        elif isinstance(source, _QdrantBackend) and source._ready:
            try:
                scroll_result = source._client.scroll(
                    collection_name=source.COLLECTION,
                    limit=10_000,
                    with_payload=True,
                )
                for point in scroll_result[0]:
                    text = point.payload.get('text', '')
                    meta = {k: v for k, v in point.payload.items()
                            if k != 'text'}
                    if text:
                        records.append((text, meta))
            except Exception as _e:
                log.debug("memory_migrate_qdrant_read_error", extra={"error": str(_e)[:120]})
        return records

    @staticmethod
    def migrate(source: MemoryBackend,
                dest:   MemoryBackend) -> int:
        """
        Migrate all records from *source* into *dest*.
        Returns the number of records successfully written.
        Records are re-embedded by *dest*'s embed function so vector
        spaces stay consistent (hash→semantic upgrade is handled).
        """
        records = MemoryMigrator._extract(source)
        count   = 0
        for text, meta in records:
            try:
                dest.store(text, meta)
                count += 1
            except Exception as exc:
                log.warning('migrator_store_failed',
                            extra={'error': str(exc), 'text_snippet': text[:60]})
        log.info('memory_migrated', extra={
            'source': type(source).__name__,
            'dest':   type(dest).__name__,
            'count':  count,
        })
        return count

    @staticmethod
    def export_jsonl(source: MemoryBackend, path: Path) -> int:
        """
        Dump all records to a JSONL file for inspection or backup.
        Returns the number of records exported.
        """
        records = MemoryMigrator._extract(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            for text, meta in records:
                fh.write(json.dumps({'text': text, 'meta': meta}) + '\n')
        log.info('memory_exported_jsonl',
                 extra={'path': str(path), 'count': len(records)})
        return len(records)

    @staticmethod
    def import_jsonl(path: Path, dest: MemoryBackend) -> int:
        """
        Import records from a JSONL file produced by export_jsonl().
        Returns the number of records imported.
        """
        count = 0
        with open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec  = json.loads(line)
                    dest.store(rec.get('text', ''),
                               rec.get('meta', {}))
                    count += 1
                except Exception as exc:
                    log.warning('migrator_import_failed',
                                extra={'error': str(exc)})
        log.info('memory_imported_jsonl',
                 extra={'path': str(path), 'count': count})
        return count





# ── ChromaDB vector backend (production semantic memory) ─────────────────────
# Activated when chromadb package is installed; falls back to _JsonMemoryBackend.
# Provides true semantic recall via cosine similarity over local embeddings.
# Embeddings: uses Ollama /api/embeddings endpoint (model nomic-embed-text)
# or falls back to TF-IDF bag-of-words when Ollama embeddings unavailable.
#
# Install: pip install chromadb
# Embedding model: ollama pull nomic-embed-text

class _ChromaMemoryBackend(MemoryBackend if hasattr(MemoryBackend, "store") else object):
    """
    ChromaDB-backed semantic memory with Ollama embeddings.
    Stores documents as embedding vectors for true cosine-similarity recall.
    """
    EMBED_MODEL = os.environ.get("Essence_EMBED_MODEL", "nomic-embed-text")
    EMBED_DIM   = 768   # nomic-embed-text output dimension

    def __init__(self, persist_dir: Path) -> None:
        self._dir   = persist_dir
        self._col   = None
        self._docs: list[dict] = []    # fallback store
        self._embed_available = False
        self._lock  = threading.Lock()
        self._init()

    def _init(self) -> None:
        try:
            import chromadb  # type: ignore
            self._client = chromadb.PersistentClient(path=str(self._dir))
            self._col    = self._client.get_or_create_collection(
                "essence_semantic",
                metadata={"hnsw:space": "cosine"})
            self._embed_available = self._check_embed_server()
            log.debug("chroma_backend_init",
                      extra={"path": str(self._dir),
                             "embed": self._embed_available})
        except ImportError:
            log.debug("chroma_not_installed",
                      extra={"hint": "pip install chromadb"})
        except Exception as e:
            log.debug("chroma_init_error", extra={"error": str(e)[:80]})

    def _check_embed_server(self) -> bool:
        try:
            urllib.request.urlopen(
                f"{_OLLAMA_HOST}/api/tags", timeout=2)
            return True
        except Exception:
            return False

    def _embed(self, text: str) -> list[float] | None:
        """Get embedding from Ollama; returns None on failure."""
        if not self._embed_available:
            return None
        try:
            payload = json.dumps({"model": self.EMBED_MODEL,
                                  "prompt": text}).encode()
            req  = urllib.request.Request(
                f"{_OLLAMA_HOST}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"})
            resp = json.loads(
                urllib.request.urlopen(req, timeout=10).read())
            return resp.get("embedding")
        except Exception as e:
            log.debug("embed_error", extra={"error": str(e)[:60]})
            return None

    # ── MemoryBackend interface ───────────────────────────────────────────────
    def store(self, text: str, metadata: dict) -> None:
        with self._lock:
            doc_id = hashlib.sha256(text.encode()).hexdigest()[:16]
            if self._col is not None:
                emb = self._embed(text)
                try:
                    if emb:
                        self._col.upsert(
                            ids=[doc_id],
                            documents=[text],
                            embeddings=[emb],
                            metadatas=[metadata or {}])
                    else:
                        self._col.upsert(
                            ids=[doc_id],
                            documents=[text],
                            metadatas=[metadata or {}])
                except Exception as e:
                    log.debug("chroma_store_error", extra={"error": str(e)[:60]})
            else:
                # Fallback: in-memory list
                self._docs.append({"id": doc_id, "text": text,
                                   "meta": metadata})

    def search(self, query: str, k: int = 5) -> list[str]:
        with self._lock:
            if self._col is not None:
                try:
                    q_emb = self._embed(query)
                    if q_emb:
                        results = self._col.query(
                            query_embeddings=[q_emb], n_results=k)
                    else:
                        results = self._col.query(
                            query_texts=[query], n_results=k)
                    return results.get("documents", [[]])[0]
                except Exception as e:
                    log.debug("chroma_search_error",
                              extra={"error": str(e)[:60]})
            # Fallback: keyword match on in-memory docs
            ql = query.lower()
            scored = [(sum(w in d["text"].lower() for w in ql.split()),
                       d["text"])
                      for d in self._docs]
            scored.sort(reverse=True)
            return [t for _, t in scored[:k] if _]

    def all_docs(self) -> list[dict]:
        if self._col is not None:
            try:
                res = self._col.get(include=["documents","metadatas"])
                return [{"text": t, "meta": m}
                        for t, m in zip(res["documents"], res["metadatas"])]
            except Exception:
                pass
        return [{"text": d["text"], "meta": d["meta"]} for d in self._docs]

    def count(self) -> int:
        if self._col is not None:
            try:
                return self._col.count()
            except Exception:
                pass
        return len(self._docs)


def _build_memory_backend(persist_dir: Path) -> "MemoryBackend":
    """
    Factory: return ChromaDB backend if available, else JSON.
    The caller (Memory class) uses this transparently.
    """
    try:
        import chromadb  # type: ignore  # noqa: F401
        return _ChromaMemoryBackend(persist_dir / "chroma")
    except ImportError:
        return _JsonMemoryBackend(persist_dir / "semantic.json")


class Memory:
    """
    Three-layer persistent memory system with optional team namespace.

    team_id="local"  → default per-user memory (backward compatible)
    team_id="acme"   → shared namespace stored at workspace/team/<team_id>/
    

    Layer 1 — Working Memory:
        Current session context. Capped at `window` recent entries.
        Fast. Cleared on consolidation into episodic.

    Layer 2 — Episodic Memory:
        Timestamped records of what happened in past sessions.
        Queryable by recency and topic.  Persisted to JSONL.

    Layer 3 — Semantic Memory:
        Distilled stable facts about the user, their projects, preferences.
        Extracted from episodic memories.  Stored in KV + vector backend.
        Injected into every system prompt.

    The `store()` / `search()` public API unchanged — callers unaffected.
    `consolidate()` advances working → episodic → semantic when called.
    `recall(query)` retrieves from all three layers with layer attribution.
    """

    def __init__(self, workspace: Path, tier: int = 0,
                 working_window: int = 20,
                 team_id: str | None = None) -> None:
        # Team namespace: shared memory lives in workspace/team/<team_id>/
        _tid = (team_id or _TEAM_ID).strip()
        if _tid and _tid != "local":
            _team_ws = workspace / "team" / _tid
            _team_ws.mkdir(parents=True, exist_ok=True)
            workspace = _team_ws
        self._team_id  = _tid
        self._ws       = workspace
        self._mem_dir  = workspace / "memory"
        self._sess_dir = workspace / "sessions"
        self._mem_dir.mkdir(parents=True, exist_ok=True)
        self._sess_dir.mkdir(parents=True, exist_ok=True)
        self._tier     = tier
        self._working_window = working_window

        # Layer 1: Working memory — in-process deque
        self._working: list[dict] = []         # {text, ts, source}
        self._working_lock = threading.Lock()

        # Layer 2: Episodic memory — append-only JSONL
        self._episodic_path = self._mem_dir / "episodic.jsonl"

        # Layer 3: Semantic memory — KV + vector backend
        self._kv_path  = self._mem_dir / "kv.json"
        self._kv: dict[str, Any] = {}
        self._kv_lock  = threading.Lock()
        self._load_kv()

        # Tiered vector backend for semantic search
        self._backend: MemoryBackend = _make_memory_backend(workspace, tier)

        # Semantic graph: simple adjacency list, stored as JSON
        self._graph_path = self._mem_dir / "semantic_graph.json"
        self._graph: dict[str, list[str]] = {}   # node → list of related nodes
        self._load_graph()

        # Layer 4 — UserProfile core block (always in-context, Letta-style)
        # A compact typed struct distilled from semantic memory and LLM extraction.
        # Injected verbatim into every system prompt via facts().
        self._profile_path = self._mem_dir / "user_profile.json"
        self._profile: dict[str, Any] = {}
        self._profile_lock = threading.Lock()
        self._load_profile()

    # ── Graph helpers ────────────────────────────────────────────────────────
    def _load_graph(self) -> None:
        if self._graph_path.exists():
            try:
                self._graph = json.loads(
                    self._graph_path.read_text(encoding="utf-8"))
            except Exception:
                self._graph = {}

    def _save_graph(self) -> None:
        tmp = self._graph_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._graph, indent=2), encoding="utf-8")
        tmp.replace(self._graph_path)

    # ── UserProfile core block ────────────────────────────────────────────────
    # Schema mirrors Letta's core memory block concept: a small, always-visible
    # typed struct the agent maintains about the user. Injected into every prompt.
    _PROFILE_SCHEMA = {
        "name": "", "occupation": "", "location": "", "timezone": "",
        "communication_style": "", "primary_language": "en",
        "current_projects": [],   # list[str]
        "preferences": {},         # dict[str, str]
        "goals": [],               # list[str]
        "notes": "",               # free-form long-term notes
    }

    def _load_profile(self) -> None:
        if self._profile_path.exists():
            try:
                with self._profile_lock:
                    self._profile = json.loads(
                        self._profile_path.read_text(encoding="utf-8"))
                return
            except Exception as _e:
                log.debug("memory_profile_load_error",
                          extra={"path": str(self._profile_path), "error": str(_e)[:120]})
        with self._profile_lock:
            self._profile = dict(self._PROFILE_SCHEMA)

    def _save_profile(self) -> None:
        tmp = self._profile_path.with_suffix(".tmp")
        with self._profile_lock:
            tmp.write_text(json.dumps(self._profile, indent=2, default=str),
                           encoding="utf-8")
        tmp.replace(self._profile_path)

    def update_profile(self, updates: dict[str, Any]) -> None:
        """Merge updates into the UserProfile and persist.
        Called by _auto_consolidate() after LLM extraction."""
        with self._profile_lock:
            for k, v in updates.items():
                if k not in self._PROFILE_SCHEMA:
                    continue
                existing = self._profile.get(k)
                if isinstance(existing, list) and isinstance(v, list):
                    # Merge lists, dedup, cap at 20 entries
                    merged = list(dict.fromkeys(existing + v))
                    self._profile[k] = merged[:20]
                elif isinstance(existing, dict) and isinstance(v, dict):
                    self._profile[k] = {**existing, **v}
                else:
                    self._profile[k] = v
        self._save_profile()

    def profile_block(self) -> str:
        """Render the UserProfile as a compact system-prompt block."""
        with self._profile_lock:
            p = dict(self._profile)
        if not any(v for v in p.values()):
            return ""
        lines = ["[User profile]"]
        for k, v in p.items():
            if not v:
                continue
            if isinstance(v, list):
                lines.append(f"  {k}: {', '.join(str(i) for i in v[:5])}")
            elif isinstance(v, dict):
                for dk, dv in list(v.items())[:5]:
                    lines.append(f"  {k}.{dk}: {dv}")
            else:
                lines.append(f"  {k}: {str(v)[:120]}")
        return "\n".join(lines)

    # ── Layer 1: Working memory ───────────────────────────────────────────────
    def working_add(self, text: str, source: str = "chat") -> None:
        with self._working_lock:
            self._working.append({"text": text, "ts": time.time(),
                                   "source": source})
            if len(self._working) > self._working_window * 2:
                self._working = self._working[-self._working_window:]
            # Auto-consolidate when window is full — promote working → episodic → semantic
            should_consolidate = (
                len(self._working) >= self._working_window
                and not getattr(self, "_consolidating", False)
            )
        if should_consolidate:
            self._consolidating = True
            _aref = getattr(self, "_agent_ref", None)
            threading.Thread(
                target=self._auto_consolidate, args=(_aref,), daemon=True).start()

    def _auto_consolidate(self, agent_ref: Any = None) -> None:
        """Background consolidation with LLM-based fact extraction (T1+).
        Flushes working → episodic, extracts durable facts → semantic + UserProfile."""
        try:
            with self._working_lock:
                entries = list(self._working)

            # Flush all to episodic first
            self.consolidate()

            if not entries:
                return

            # T1+ only: use CONSOLIDATOR specialist for LLM-based extraction
            consolidator = None
            if agent_ref is not None and self._tier >= 1:
                pool = getattr(agent_ref, "_specialist_pool", {})
                consolidator = pool.get(AgentRole.CONSOLIDATOR) if pool else None

            if consolidator:
                combined = " | ".join(e["text"][:200] for e in entries[-12:])
                extraction_prompt = (
                    "You are a memory extraction assistant.\n"
                    "Given this conversation snippet, extract two things:\n"
                    "1. A JSON list (key 'facts') of 0-3 durable facts worth "
                    "remembering long-term. If nothing is worth keeping, use [].\n"
                    "2. A JSON object (key 'profile') with any UserProfile fields "
                    "you can infer: name, occupation, location, timezone, "
                    "communication_style, primary_language, current_projects (list), "
                    "preferences (dict), goals (list), notes. Only include fields "
                    "with confident values; omit the rest.\n"
                    "Return ONLY valid JSON: "
                    '{"facts": [...], "profile": {...}}\n\n'
                    f"Snippet:\n{combined}"
                )
                try:
                    raw = consolidator.run(extraction_prompt)
                    clean = re.sub(r"```[a-zA-Z]*", "", raw).strip()
                    extracted = json.loads(clean)
                    # v23: Schema-validate consolidation output
                    _vok, _verr = SCHEMA_REGISTRY.validate("consolidation_output", extracted)
                    if not _vok:
                        log.debug("consolidation_schema_mismatch", extra={"error": _verr[:80]})
                    # Store facts in vector backend (existing)
                    for fact in extracted.get("facts", []):
                        if isinstance(fact, str) and fact.strip():
                            self._backend.store(
                                fact[:500],
                                {"layer": "semantic", "type": "llm_extracted"})

                    # v20: also assert any triples the LLM produced into SSS
                    if agent_ref is not None:
                        _sss = getattr(agent_ref, "_semantic_state", None)
                        for t in extracted.get("triples", [])[:20]:
                            if not isinstance(t, dict): continue
                            try:
                                _sss.assert_fact(
                                    entity    = str(t.get("entity",    "user")),
                                    relation  = str(t.get("relation",  "note")),
                                    attribute = str(t.get("attribute", "fact")),
                                    value     = str(t.get("value",     ""))[:300],
                                    confidence= float(t.get("confidence", 0.7)),
                                    source    = "auto_consolidation",
                                )
                            except Exception:
                                pass

                    profile_updates = extracted.get("profile", {})
                    if profile_updates and isinstance(profile_updates, dict):
                        self.update_profile(profile_updates)
                except Exception as e:
                    log.debug("llm_extraction_failed", extra={"error": str(e)[:80]})
                    # Graceful fallback: store combined text verbatim
                    self._backend.store(
                        combined[:500],
                        {"layer": "semantic", "type": "bulk_fallback"})
            else:
                # T0 / no-specialist fallback: keyword heuristic (original logic)
                _SIGNAL_WORDS = frozenset([
                    "remember", "always", "never", "prefer", "name", "called",
                    "version", "deadline", "project", "todo", "goal", "important",
                    "password", "key", "config", "setting",
                ])
                for entry in entries:
                    words = set(entry["text"].lower().split())
                    score = sum(1 for w in words if w in _SIGNAL_WORDS)
                    if "?" in entry["text"]:
                        score += 1
                    if score >= 2:
                        self._backend.store(
                            entry["text"][:500],
                            {"layer": "semantic", "type": "keyword_promoted",
                             "source": entry.get("source", "")})
        finally:
            self._consolidating = False

    def working_context(self, k: int = 5) -> list[str]:
        with self._working_lock:
            return [e["text"] for e in self._working[-k:]]

    # ── Layer 2: Episodic memory ──────────────────────────────────────────────
    def _get_ep_store(self) -> "EpisodicStore":
        """Lazy-init EpisodicStore for this workspace."""
        if not hasattr(self, "_ep_store") or self._ep_store is None:
            self._ep_store = EpisodicStore(self._ws)
        return self._ep_store

    def record_episode(self, text: str, metadata: dict | None = None) -> None:
        # v27: Delegate to EpisodicStore (SQLite WAL) for concurrent-write safety
        try:
            self._get_ep_store().record(
                text[:2000],
                session_id=getattr(self, "_session_id", ""),
                metadata=metadata)
        except Exception as _e:
            # Fallback to plain JSONL on any error
            log.debug("episodic_store_fallback", extra={"error": str(_e)[:80]})
            record = json.dumps({"text": text[:2000], "ts": time.time(),
                                  "meta": metadata or {}})
            with open(self._episodic_path, "a", encoding="utf-8", buffering=1) as f:
                f.write(record + "\n")

    def recent_episodes(self, n: int = 10) -> list[dict]:
        # v27: Delegate to EpisodicStore (SQLite WAL)
        try:
            rows = self._get_ep_store().recent(n)
            if rows:
                return rows
        except Exception:
            pass
        # Fallback: plain JSONL for workspaces not yet migrated
        if not self._episodic_path.exists():
            return []
        lines = self._episodic_path.read_text(encoding="utf-8").splitlines()
        records = []
        for line in reversed(lines[-n * 3:]):
            try:
                records.append(json.loads(line))
                if len(records) >= n:
                    break
            except Exception as _e:
                log.debug("memory_episode_parse_error", extra={"error": str(_e)[:80]})
        return list(reversed(records))

    # ── Layer 3: Semantic memory (KV + vector) ────────────────────────────────
    def _load_kv(self) -> None:
        if self._kv_path.exists():
            try:
                self._kv = json.loads(self._kv_path.read_text(encoding="utf-8"))
            except Exception as _e:
                log.debug("memory_kv_load_error",
                          extra={"path": str(self._kv_path), "error": str(_e)[:120]})
                self._kv = {}

    def _save_kv(self) -> None:
        tmp = self._kv_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._kv, indent=2, default=str),
                       encoding="utf-8")
        tmp.replace(self._kv_path)

    def set(self, key: str, value: Any) -> None:
        with self._kv_lock:
            self._kv[key] = value
            self._save_kv()
        self._backend.store(f"{key}: {value}", {"key": key, "layer": "semantic"})

    def get(self, key: str, default: Any = None) -> Any:
        with self._kv_lock:
            return self._kv.get(key, default)

    def store(self, text: str, metadata: dict | None = None) -> None:
        """Store text in vector backend (semantic layer) and working layer."""
        self._backend.store(text, metadata)
        self.working_add(text, source=(metadata or {}).get("source", "store"))

    def search(self, query: str, k: int = 5) -> list[str]:
        """Semantic search over semantic layer (vector backend)."""
        return self._backend.search(query, k)

    def recall(self, query: str, k: int = 6) -> dict[str, list[str]]:
        """
          MULTI-LAYER RECALL  — working · episodic · semantic

        Returns {"working": [...], "episodic": [...], "semantic": [...]}

        All three layers use relevance scoring (not substring matching):
          - Working  : TF-IDF cosine similarity over recent in-memory items
          - Episodic : TF-IDF cosine similarity over recent JSONL episodes
          - Semantic : vector backend (faiss/qdrant/JSON BM25)

        Falls back to keyword overlap when sklearn is unavailable.
        """

        def _score_texts(texts: list[str], q: str, top_k: int) -> list[str]:
            """Return top_k texts scored by TF-IDF cosine similarity to q."""
            if not texts:
                return []
            try:
                from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
                import numpy as np
                corpus = texts + [q]
                vect   = TfidfVectorizer(min_df=1, stop_words="english")
                tfidf  = vect.fit_transform(corpus)
                scores = (tfidf[:-1] @ tfidf[-1].T).toarray().ravel()
                ranked = sorted(range(len(texts)), key=lambda i: -scores[i])
                return [texts[i] for i in ranked[:top_k] if scores[i] > 0]
            except ImportError:
                # Keyword overlap fallback (T0 / no sklearn)
                q_words = set(q.lower().split())
                def _overlap(t: str) -> int:
                    return len(q_words & set(t.lower().split()))
                ranked = sorted(range(len(texts)), key=lambda i: -_overlap(texts[i]))
                return [texts[i] for i in ranked[:top_k] if _overlap(texts[i]) > 0]

        # Working layer
        with self._working_lock:
            working_texts = [e["text"] for e in self._working]
        working_hits = _score_texts(working_texts, query, k)

        # Episodic layer
        episodes      = self.recent_episodes(50)
        episodic_texts = [e["text"] for e in episodes]
        episodic_hits  = _score_texts(episodic_texts, query, k)

        # Semantic layer (vector backend)
        semantic_hits = self.search(query, k)

        return {"working":  working_hits,
                "episodic": episodic_hits,
                "semantic": semantic_hits}

    def consolidate(self, summary: str = "") -> None:
        """
        Advance memories:  working → episodic, episodic summary → semantic.
        Called by Agent._maybe_distil() after every memory_window turns.
        """
        # Flush working memory to episodic
        with self._working_lock:
            if self._working:
                combined = " | ".join(e["text"][:100] for e in self._working[-10:])
                self.record_episode(combined, {"source": "working_flush"})
                self._working.clear()

        # Promote summary to semantic KV if provided
        if summary:
            self.set("last_summary", summary[:500])
            self._backend.store(summary, {"layer": "semantic", "type": "summary"})

    # ── Session transcripts ───────────────────────────────────────────────────
    def facts(self) -> str:
        parts: list[str] = []
        # Pinned UserProfile block — always first, always in-context
        profile = self.profile_block()
        if profile:
            parts.append(profile)
        with self._kv_lock:
            if self._kv:
                lines = ["[Memory]"]
                for k, v in list(self._kv.items())[-20:]:
                    lines.append(f"  {k}: {str(v)[:100]}")
                parts.append("\n".join(lines))
        # Append recent episodes as context
        episodes = self.recent_episodes(3)
        if episodes:
            lines = ["[Recent episodes]"]
            for ep in episodes:
                lines.append(f"  {ep['text'][:120]}")
            parts.append("\n".join(lines))
        return "\n".join(parts)

    def append_session(self, session_id: str, role: str,
                        content: str) -> None:
        path = self._sess_dir / f"{session_id}.jsonl"
        record = json.dumps({"role": role, "content": content,
                              "ts": time.time()})
        with open(path, "a", encoding="utf-8", buffering=1) as f:
            f.write(record + "\n")
        # Mirror to working memory
        self.working_add(content[:200], source=role)

    def load_session(self, session_id: str,
                     strip_ts: bool = True) -> list[dict]:
        path = self._sess_dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if strip_ts:
                    r = {k: v for k, v in r.items() if k != "ts"}
                records.append(r)
            except Exception as _e:
                log.debug("memory_session_parse_error",
                          extra={"session": session_id, "error": str(_e)[:80]})
        return records

    # ── Layer 4: Semantic graph (concept → related concepts) ─────────────────
    def link(self, concept: str, related: str) -> None:
        """
        Record a directed association: concept → related.
        Stored in the KV layer under "_graph:<concept>" as a set of strings.
        Bidirectional links require two calls.

        Example::
            mem.link("Python", "programming")
            mem.link("Python", "data science")
        """
        key  = f"_graph:{concept.lower().strip()}"
        with self._profile_lock:
            edges: list[str] = list(self.get(key, []))
            rel = related.strip()
            if rel not in edges:
                edges.append(rel)
            self.set(key, edges)

    def related(self, concept: str, depth: int = 1) -> list[str]:
        """
        Return concepts reachable from *concept* within *depth* hops.

        depth=1 returns direct neighbours only.
        depth=2 also returns their neighbours (BFS, deduped, concept excluded).

        Example::
            mem.link("Python", "programming")
            mem.link("Python", "data science")
            assert "programming" in mem.related("Python", depth=1)
        """
        visited: set[str] = set()
        frontier: set[str] = {concept.lower().strip()}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for node in frontier:
                key   = f"_graph:{node}"
                edges = list(self.get(key, []))
                for e in edges:
                    e_low = e.lower().strip() if isinstance(e, str) else str(e)
                    if e_low not in visited and e_low != concept.lower().strip():
                        visited.add(e_low)
                        next_frontier.add(e_low)
            frontier = next_frontier
        # Return in original-case form by doing a case-insensitive lookup
        # through stored edge lists so callers get back the original strings.
        result: list[str] = []
        seen_low: set[str] = set()
        root_key = f"_graph:{concept.lower().strip()}"
        # BFS again collecting original-case strings
        bfs_queue = [concept.lower().strip()]
        bfs_visited: set[str] = {concept.lower().strip()}
        for hop in range(depth):
            next_q: list[str] = []
            for node in bfs_queue:
                for raw_edge in list(self.get(f"_graph:{node}", [])):
                    raw_low = raw_edge.lower().strip() if isinstance(raw_edge, str) else str(raw_edge)
                    if raw_low not in bfs_visited and raw_low != concept.lower().strip():
                        bfs_visited.add(raw_low)
                        next_q.append(raw_low)
                        raw_str = raw_edge if isinstance(raw_edge, str) else str(raw_edge)
                        if raw_str not in seen_low:
                            seen_low.add(raw_str)
                            result.append(raw_str)
            bfs_queue = next_q
        return result


    # ── Data sovereignty: export / import bundle ──────────────────────────────

    def export_bundle(self, passphrase: str = "") -> bytes:
        """
        Export the full memory state as an encrypted portable bundle.

        The bundle contains: episodic log, semantic KV, graph edges, profile,
        and all session JSONL files. Encrypted with AES-256-GCM when the
        `cryptography` package is available; XOR-encrypted otherwise (DEV ONLY).

        Returns raw bytes suitable for writing to a .essence_bundle file.

        Usage::
            bundle = mem.export_bundle(passphrase="my-secret")
            Path("backup.essence_bundle").write_bytes(bundle)
        """
        import zipfile, io, tempfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Episodic log
            if self._episodic_path.exists():
                zf.write(self._episodic_path, "episodic.jsonl")
            # KV store
            if self._kv_path.exists():
                zf.write(self._kv_path, "kv.json")
            # Profile
            if self._profile_path.exists():
                zf.write(self._profile_path, "profile.json")
            # Sessions
            if self._sess_dir.exists():
                for p in self._sess_dir.glob("*.jsonl"):
                    zf.write(p, f"sessions/{p.name}")
            # Graph edges (from KV — keys starting with _graph:)
            graph_data = {k: v for k, v in self._kv.items()
                          if k.startswith("_graph:")}
            if graph_data:
                zf.writestr("graph.json", json.dumps(graph_data, indent=2))
            # Manifest
            manifest = {
                "version": Essence_VERSION,
                "team_id": self._team_id,
                "exported_at": time.time(),
                "tier": self._tier,
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        raw = buf.getvalue()
        if not passphrase:
            return raw
        # Encrypt the bundle using PBKDF2+AES-GCM with a random salt embedded in header.
        # Format: MAGIC(4) + SALT(32) + encrypted_payload
        _BUNDLE_MAGIC = b"UAIB"
        import hashlib as _hl, secrets as _sec
        salt = _sec.token_bytes(32)
        key  = _hl.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt,
                                 SecretsVault._ITER, dklen=32)
        if _AESGCM:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM_cls
            nonce = _sec.token_bytes(12)
            ct    = _AESGCM_cls(key).encrypt(nonce, raw, None)
            encrypted = nonce + ct
        else:
            # XOR stream (DEV ONLY)
            stream = b""
            block  = key
            while len(stream) < len(raw):
                block  = _hl.sha256(block).digest()
                stream += block
            encrypted = bytes(a ^ b for a, b in zip(raw, stream[:len(raw)]))
        return _BUNDLE_MAGIC + salt + encrypted

    def import_bundle(self, bundle_bytes: bytes,
                      passphrase: str = "",
                      merge: bool = False) -> dict:
        """
        Import a memory bundle exported by export_bundle().

        merge=False  (default) replaces existing memory.
        merge=True   appends episodic records and merges KV/graph.

        Returns a summary dict with counts of imported records.
        """
        import zipfile, io, tempfile
        raw = bundle_bytes
        if passphrase:
            _BUNDLE_MAGIC = b"UAIB"
            if bundle_bytes[:4] == _BUNDLE_MAGIC:
                # Versioned encrypted bundle — decode header
                import hashlib as _hl
                salt      = bundle_bytes[4:36]
                encrypted = bundle_bytes[36:]
                key = _hl.pbkdf2_hmac("sha256", passphrase.encode("utf-8"),
                                       salt, SecretsVault._ITER, dklen=32)
                try:
                    if _AESGCM:
                        from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AG
                        nonce, ct = encrypted[:12], encrypted[12:]
                        raw = _AG(key).decrypt(nonce, ct, None)
                    else:
                        stream = b""
                        block  = key
                        while len(stream) < len(encrypted):
                            block  = _hl.sha256(block).digest()
                            stream += block
                        raw = bytes(a ^ b for a, b in zip(encrypted, stream[:len(encrypted)]))
                except Exception as _e:
                    raise ValueError(f"Bundle decryption failed: {_e}") from _e
            # else: no magic header → treat as unencrypted (backward compat)

        buf = io.BytesIO(raw)
        counts = {"episodic": 0, "sessions": 0, "kv_keys": 0, "graph_edges": 0}
        try:
            with zipfile.ZipFile(buf, "r") as zf:
                names = zf.namelist()
                # Episodic
                if "episodic.jsonl" in names:
                    lines = zf.read("episodic.jsonl").decode("utf-8",
                                                              errors="replace")
                    mode = "a" if merge else "w"
                    with self._episodic_path.open(mode, encoding="utf-8") as fh:
                        fh.write(lines)
                    counts["episodic"] = lines.count("\n")
                # KV
                if "kv.json" in names:
                    imported_kv = json.loads(
                        zf.read("kv.json").decode("utf-8"))
                    if merge:
                        self._kv.update(imported_kv)
                    else:
                        self._kv = imported_kv
                    self._save_kv()
                    counts["kv_keys"] = len(imported_kv)
                # Graph
                if "graph.json" in names:
                    graph = json.loads(zf.read("graph.json").decode("utf-8"))
                    for k, v in graph.items():
                        if merge and k in self._kv:
                            existing = list(self._kv.get(k, []))
                            for edge in (v if isinstance(v, list) else [v]):
                                if edge not in existing:
                                    existing.append(edge)
                            self._kv[k] = existing
                        else:
                            self._kv[k] = v
                        counts["graph_edges"] += len(v) if isinstance(v, list) else 1
                    self._save_kv()
                # Sessions
                for name in names:
                    if name.startswith("sessions/") and name.endswith(".jsonl"):
                        dest = self._sess_dir / Path(name).name
                        content = zf.read(name).decode("utf-8", errors="replace")
                        mode = "a" if (merge and dest.exists()) else "w"
                        dest.write_text(content, encoding="utf-8")
                        counts["sessions"] += 1
        except zipfile.BadZipFile as e:
            raise ValueError(f"Invalid bundle format: {e}") from e

        log.info("memory_bundle_imported", extra=counts)
        return counts



# ══════════════════════════════════════════════════════════════════════════════
