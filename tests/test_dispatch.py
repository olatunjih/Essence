"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *
from essence.tools.registry import BUILTIN_TOOLS  # noqa: F401  [auto-fix]
from essence.tools.registry import _tool_shell, _tool_read, _tool_write, _tool_python, _tool_search  # noqa: F401  [auto-fix]
import unittest.mock as _mock  # noqa: F401  [auto-fix]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   Tool dispatch ─────────────────────────────────────────────────────

def test_tool_shell_executes_safe_command(tmp_path):
    result = _tool_shell('echo hello_world', workspace=tmp_path)
    assert 'hello_world' in result


def test_tool_shell_blocks_rm_rf(tmp_path):
    result = _tool_shell('rm -rf /', workspace=tmp_path)
    assert 'BLOCKED' in result


def test_tool_read_returns_file_content(tmp_path):
    p = tmp_path / 'test.txt'
    p.write_text('test content', encoding='utf-8')
    result = _tool_read(str(p))
    assert result == 'test content'


def test_tool_write_creates_file(tmp_path):
    path   = str(tmp_path / 'out.txt')
    result = _tool_write(path, 'written content', workspace=tmp_path)
    assert 'wrote' in result
    assert Path(path).read_text() == 'written content'


def test_tool_write_blocked_outside_workspace(tmp_path):
    result = _tool_write('/etc/hosts', 'bad', workspace=tmp_path)
    assert 'BLOCKED' in result


def test_tool_python_exec_returns_stdout():
    result = _tool_python("print('hello_from_sandbox')", timeout=10)
    assert 'hello_from_sandbox' in result


def test_tool_python_exec_blocks_socket_import():
    result = _tool_python('import socket; socket.socket()', timeout=10)
    assert any(kw in result.lower()
               for kw in ('sandbox', 'not allowed', 'error', 'importerror'))


def test_tool_search_returns_string_even_offline():
    with _mock.patch('urllib.request.urlopen',
                     side_effect=Exception('no network')):
        result = _tool_search('python programming')
    assert isinstance(result, str)


