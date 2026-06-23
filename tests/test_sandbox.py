"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   Memory backends ───────────────────────────────────────────────────

def test_json_memory_store_and_search(tmp_path):
    mem = _JsonMemoryBackend(tmp_path / 'kv.json')
    mem.store('The Eiffel Tower is in Paris', {'source': 'test'})
    mem.store('Python is a programming language', {'source': 'test'})
    results = mem.search('Eiffel Tower Paris', k=5)
    assert any('Eiffel' in r for r in results)


def test_json_memory_persists_to_disk(tmp_path):
    p    = tmp_path / 'kv.json'
    mem  = _JsonMemoryBackend(p)
    mem.store('persistent fact', {})
    mem2 = _JsonMemoryBackend(p)
    results = mem2.search('persistent fact')
    assert any('persistent' in r for r in results)


def test_memory_class_kv_round_trip(tmp_path):
    mem = Memory(tmp_path, tier=0)
    mem.set('key1', 'value1')
    assert mem.get('key1') == 'value1'
    assert mem.get('missing', 'default') == 'default'


def test_memory_session_append_and_load(tmp_path):
    mem = Memory(tmp_path, tier=0)
    mem.append_session('sess1', 'user', 'hello')
    mem.append_session('sess1', 'assistant', 'hi there')
    records = mem.load_session('sess1')
    assert len(records) == 2
    assert records[0]['role'] == 'user'
    assert records[1]['content'] == 'hi there'


