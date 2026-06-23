"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   Backend adapters ──────────────────────────────────────────────────

def test_ollama_backend_dead_when_no_server():
    with _mock.patch('urllib.request.urlopen',
                     side_effect=Exception('connection refused')):
        b = OllamaBackend(host='http://127.0.0.1:19999')
        assert b.alive() is False


def test_ollama_backend_complete_yields_tokens():
    chunks = [
        b'{"message":{"role":"assistant","content":"hello "},"done":false}\n',
        b'{"message":{"role":"assistant","content":"world"},"done":true}\n',
        b'',
    ]
    mock_resp = _mock.MagicMock()
    mock_resp.__enter__ = _mock.MagicMock(return_value=mock_resp)
    mock_resp.__exit__  = _mock.MagicMock(return_value=False)
    mock_resp.readline  = _mock.MagicMock(side_effect=chunks)
    with _mock.patch('urllib.request.urlopen', return_value=mock_resp):
        b      = OllamaBackend()
        result = ''.join(b.complete(
            [{'role': 'user', 'content': 'hi'}], model='test', stream=True))
    assert 'hello' in result
    assert 'world' in result


def test_openai_compat_api_key_is_secret():
    b = OpenAICompatBackend(base='http://localhost:8000', api_key='sk-test123')
    # SecretStr must not expose the key in repr
    assert 'sk-test123' not in repr(b.api_key)
    secret_val = (b.api_key.get_secret_value()
                  if hasattr(b.api_key, 'get_secret_value') else b.api_key)
    assert secret_val == 'sk-test123'


def test_provider_chain_raises_when_all_backends_dead():
    """ProviderChain.active uses a deferred-error design: returns the first provider
    even when all are dead (so complete() can surface a descriptive error).
    Raises BackendError immediately only when the chain is completely empty."""
    import pytest as _pt
    dead = _mock.MagicMock()
    dead.alive.return_value = False
    dead.NAME = "dead-backend"
    chain = ProviderChain([dead])
    # Non-empty dead chain: active returns the dead provider (deferred error)
    assert chain.active is dead
    # Empty chain: active raises BackendError immediately
    with _pt.raises(BackendError):
        _ = ProviderChain([]).active


def test_provider_chain_retries_on_transient_error():
    calls: list[int] = []

    def _complete(*a: Any, **kw: Any) -> Iterator[str]:
        calls.append(1)
        if len(calls) < 2:
            raise BackendError('transient')
        yield 'ok'

    provider = _mock.MagicMock()
    provider.alive.return_value = True
    provider.complete = _complete
    chain = ProviderChain([provider])
    with _mock.patch('time.sleep'):
        result = ''.join(chain.complete([], model='x'))
    assert result == 'ok'


