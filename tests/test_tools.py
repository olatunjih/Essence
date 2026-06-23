"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   MemoryMigrator ────────────────────────────────────────────────────

def test_memory_migrator_json_to_json(tmp_path):
    src = _JsonMemoryBackend(tmp_path / 'src.json')
    dst = _JsonMemoryBackend(tmp_path / 'dst.json')
    src.store('fact one', {})
    src.store('fact two', {})
    n = MemoryMigrator.migrate(src, dst)
    assert n == 2
    results = dst.search('fact')
    assert len(results) >= 1


def test_memory_migrator_export_import_jsonl(tmp_path):
    src  = _JsonMemoryBackend(tmp_path / 'src.json')
    src.store('alpha text', {})
    src.store('beta text', {})
    out  = tmp_path / 'export.jsonl'
    n    = MemoryMigrator.export_jsonl(src, out)
    assert n == 2 and out.exists()
    dst  = _JsonMemoryBackend(tmp_path / 'dst.json')
    ni   = MemoryMigrator.import_jsonl(out, dst)
    assert ni == 2
    results = dst.search('alpha')
    assert any('alpha' in r for r in results)


