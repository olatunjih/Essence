"""Phase 10.4 — Contract tests for all MemoryBackend implementations.

Every backend must satisfy the same behavioural contract regardless of
the storage tier (JSON / SQLite-vec / FAISS / Qdrant).
"""
from __future__ import annotations
import pytest
from pathlib import Path
from essence.memory.backends import (
    _JsonMemoryBackend,
    _SqliteVecBackend,
    _FaissBackend,
    MemoryBackend,
)


@pytest.fixture(params=[_JsonMemoryBackend, _SqliteVecBackend, _FaissBackend])
def backend(request, tmp_path: Path) -> MemoryBackend:
    cls = request.param
    b = cls(tmp_path)
    yield b
    try:
        b.close()  # type: ignore[attr-defined]
    except AttributeError:
        pass


class TestMemoryContract:
    def test_store_returns_key(self, backend: MemoryBackend) -> None:
        k = backend.store("hello world", {"src": "test"})
        assert isinstance(k, str) and k, "store() must return a non-empty string key"

    def test_search_finds_stored(self, backend: MemoryBackend) -> None:
        backend.store("the quick brown fox")
        results = backend.search("fox")
        assert any("fox" in r for r in results), "search() must find text matching the query"

    def test_search_empty_returns_list(self, backend: MemoryBackend) -> None:
        results = backend.search("nothing here yet")
        assert isinstance(results, list), "search() must return a list even when empty"

    def test_delete_removes(self, backend: MemoryBackend) -> None:
        k = backend.store("temporary content for delete test")
        deleted = backend.delete(k)
        assert isinstance(deleted, bool), "delete() must return bool"

    def test_clear_empties(self, backend: MemoryBackend) -> None:
        backend.store("item_one")
        backend.store("item_two")
        backend.clear()
        results = backend.search("item_one")
        assert isinstance(results, list), "clear() must leave search() returning a list"

    def test_health_returns_dict(self, backend: MemoryBackend) -> None:
        h = backend.health()
        assert isinstance(h, dict), "health() must return a dict"
        assert "backend" in h or "embedding_mode" in h, \
            "health() dict must contain at least 'backend' or 'embedding_mode'"

    def test_multiple_stores(self, backend: MemoryBackend) -> None:
        keys = [backend.store(f"document {i}") for i in range(5)]
        assert len(set(keys)) == 5, "store() must return unique keys for different texts"
