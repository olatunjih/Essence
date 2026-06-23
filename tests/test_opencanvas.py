"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   AgentConfig validation ───────────────────────────────────────────

def test_agent_config_rejects_invalid_autonomy():
    import pytest as _pt
    with _pt.raises(Exception):
        AgentConfig(
            provider=_mock.MagicMock(), model='test',
            workspace=Path('/tmp'), autonomy_level=5)


def test_agent_config_rejects_low_budget():
    import pytest as _pt
    with _pt.raises(Exception):
        AgentConfig(
            provider=_mock.MagicMock(), model='test',
            workspace=Path('/tmp'), budget=10)


