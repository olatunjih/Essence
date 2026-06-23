"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *
from essence.memory.migrator import _make_memory_backend  # noqa: F401  [auto-fix]  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]
from essence.workspace.ingestor import DocumentIngestor  # noqa: F401  [auto-fix]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   Document ingestor ─────────────────────────────────────────────────

def test_document_ingestor_ingests_txt(tmp_path):
    mem      = Memory(tmp_path, tier=0)
    p        = tmp_path / 'doc.txt'
    p.write_text('Machine learning is a subset of AI.', encoding='utf-8')
    ingestor = DocumentIngestor(mem, tmp_path)
    n        = ingestor.ingest(p)
    assert n >= 1


def test_document_ingestor_chunks_long_text(tmp_path):
    mem      = Memory(tmp_path, tier=0)
    ingestor = DocumentIngestor(mem, tmp_path)
    chunks   = ingestor._chunk('a' * 2000)
    assert len(chunks) > 1


def test_document_ingestor_rejects_unsupported_format(tmp_path):
    mem      = Memory(tmp_path, tier=0)
    p        = tmp_path / 'file.xyz'
    p.write_text('xyz content')
    ingestor = DocumentIngestor(mem, tmp_path)
    assert ingestor.ingest(p) == 0


