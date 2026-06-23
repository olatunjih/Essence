"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *
from essence.agents.critic import _synthesise_constraints  # noqa: F401  [auto-fix]
from essence.agents.critic import CriticResult  # noqa: F401  [auto-fix]  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   CriticGate critic ───────────────────────────────────────────────────

async def test_critic_result_parses_valid_pass():
    raw = '{"pass": true, "category": null, "evidence": null, "fix_hint": null}'
    cr  = await CriticResult.from_json(raw)
    assert cr.passed is True and cr.category is None


async def test_critic_result_parses_valid_fail():
    raw = ('{"pass": false, "category": "ToolMisuse", '
           '"evidence": "wrong arg", "fix_hint": "use correct arg"}')
    cr  = await CriticResult.from_json(raw)
    assert cr.passed is False
    assert cr.category == 'ToolMisuse'
    assert cr.evidence == 'wrong arg'


async def test_critic_result_safe_blocks_on_bad_json():
    cr = await CriticResult.from_json('this is not json at all')
    assert cr.passed is False
    assert cr.category == 'FormatError'


def test_synthesise_constraints_parses_tools_md():
    tools_md    = '## Constraints\n- Never delete files\n- Always show plan\n'
    constraints = _synthesise_constraints(tools_md)
    assert 'Never delete files' in constraints
    assert 'Always show plan' in constraints


