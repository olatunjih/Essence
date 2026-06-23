"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   Agent chat ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_chat_accumulates_history(tmp_path):
    mock_prov = _mock.MagicMock()
    mock_prov.acomplete.return_value = _async_iter(["Hello ", "world"])
    cfg    = AgentConfig(provider=mock_prov, model="test", workspace=tmp_path)
    agent  = Agent(cfg)
    result = await agent.chat("Hi there")
    assert "Hello" in result
    assert len(agent.history) == 2
def test_agent_chat_calls_emit_callback(tmp_path):
    mock_prov = _mock.MagicMock()
    mock_prov.complete.return_value = iter(['tok1', 'tok2'])
@pytest.mark.asyncio
async def test_agent_chat_calls_emit_callback(tmp_path):
    mock_prov = _mock.MagicMock()
    mock_prov.complete.return_value = iter(["tok1", "tok2"])
    cfg      = AgentConfig(provider=mock_prov, model="test", workspace=tmp_path)
    mock_prov.acomplete.return_value = _async_iter(["tok1", "tok2"])
    agent = Agent(cfg)
    received: list[str] = []
    await agent.chat("test", emit=received.append)
    assert received == ["tok1", "tok2"]
@pytest.mark.asyncio
async def test_agent_memory_distillation_trims_history(tmp_path):
    mock_prov = _mock.MagicMock()
    mock_prov.complete.return_value = iter(['summary text'])
    cfg   = AgentConfig(
        provider=mock_prov, model='test',
        workspace=tmp_path, memory_window=2)
    agent = Agent(cfg)
    agent.history = [
        {'role': 'user',      'content': 'msg1'},
        {'role': 'assistant', 'content': 'rep1'},
    ]
    await agent._maybe_distil()
    assert len(agent.history) <= 3


