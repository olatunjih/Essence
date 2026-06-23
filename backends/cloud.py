"""Cloud LLM provider backends — OpenAI, Anthropic, Gemini, Perplexity, Custom.

Each provider implements the same InferenceProvider protocol as the local
backends so they slot into ProviderChain and the smart router transparently.

Multi-provider parallel availability:
    from essence.backends.cloud import build_cloud_providers
    providers = build_cloud_providers(store)   # auto-reads credentials
"""
from __future__ import annotations

import json
import logging
import os
import time
import threading
import urllib.request
import urllib.error
from typing import Iterator, AsyncIterator, Any

log = logging.getLogger("essence.backends.cloud")

_ALIVE_CACHE: dict[str, tuple[bool, float]] = {}
_ALIVE_LOCK  = threading.Lock()
_ALIVE_TTL   = 30.0


def _ping_url(url: str, headers: dict | None = None,
              timeout: int = 6) -> bool:
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False


def _ping_cached(key: str, url: str,
                 headers: dict | None = None) -> bool:
    now = time.monotonic()
    with _ALIVE_LOCK:
        cached = _ALIVE_CACHE.get(key)
        if cached and now < cached[1]:
            return cached[0]
    result = _ping_url(url, headers)
    with _ALIVE_LOCK:
        _ALIVE_CACHE[key] = (result, now + _ALIVE_TTL)
    return result


def _sse_stream(url: str, payload: dict,
                headers: dict) -> Iterator[str]:
    """POST url with payload, yield SSE token strings."""
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data,
                                  headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    try:
                        tok = (json.loads(line[6:])["choices"][0]
                               .get("delta", {}).get("content", ""))
                        if tok:
                            yield tok
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
    except urllib.error.URLError as exc:
        raise RuntimeError(f"POST {url}: {exc}") from exc


# ── OpenAI ────────────────────────────────────────────────────────────────────

class OpenAIProvider:
    NAME = "openai"

    def __init__(self, api_key: str,
                 base_url: str = "https://api.openai.com",
                 org_id: str = "") -> None:
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")
        self.org_id   = org_id
        self._models: list[str] = []

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json",
             "Authorization": f"Bearer {self.api_key}"}
        if self.org_id:
            h["OpenAI-Organization"] = self.org_id
        return h

    def alive(self) -> bool:
        return _ping_cached(
            f"openai:{self.base_url}",
            f"{self.base_url}/v1/models",
            self._headers(),
        )

    def list_models(self) -> list[str]:
        if self._models:
            return self._models
        try:
            req = urllib.request.Request(
                f"{self.base_url}/v1/models",
                headers=self._headers())
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
                self._models = [m["id"] for m in data.get("data", [])]
                return self._models
        except Exception:
            return []

    def complete(self, messages: list[dict], *, model: str,
                 stream: bool = True, thinking: bool = False,
                 budget: int = 4096,
                 tools: list[dict] | None = None) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": model, "messages": messages,
            "stream": stream, "max_tokens": budget or 4096,
            "temperature": 0.6 if thinking else 0.7,
        }
        if tools:
            payload["tools"] = tools
        yield from _sse_stream(
            f"{self.base_url}/v1/chat/completions",
            payload, self._headers())

    async def acomplete(self, messages: list[dict], *, model: str,
                        stream: bool = True, thinking: bool = False,
                        budget: int = 4096,
                        tools: list[dict] | None = None) -> AsyncIterator[str]:
        import asyncio
        loop = asyncio.get_running_loop()
        gen  = self.complete(messages, model=model, stream=stream,
                             thinking=thinking, budget=budget, tools=tools)
        while True:
            try:
                tok = await loop.run_in_executor(None, next, gen)
                yield tok
            except StopIteration:
                break


# ── Anthropic ─────────────────────────────────────────────────────────────────

class AnthropicProvider:
    NAME = "anthropic"
    _BASE = "https://api.anthropic.com"

    def __init__(self, api_key: str,
                 base_url: str = "") -> None:
        self.api_key  = api_key
        self.base_url = (base_url or self._BASE).rstrip("/")
        self._models: list[str] = []

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
        }

    def alive(self) -> bool:
        return _ping_cached(
            "anthropic",
            f"{self.base_url}/v1/models",
            self._headers(),
        )

    def list_models(self) -> list[str]:
        if self._models:
            return self._models
        try:
            req = urllib.request.Request(
                f"{self.base_url}/v1/models",
                headers=self._headers())
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
                self._models = [m["id"] for m in data.get("data", [])]
                return self._models
        except Exception:
            return ["claude-opus-4-5", "claude-sonnet-4-5",
                    "claude-3-5-haiku-20241022"]

    def complete(self, messages: list[dict], *, model: str,
                 stream: bool = True, thinking: bool = False,
                 budget: int = 4096,
                 tools: list[dict] | None = None) -> Iterator[str]:
        # Convert system message for Anthropic format
        system_text = ""
        user_msgs   = []
        for m in messages:
            if m["role"] == "system":
                system_text = m.get("content", "")
            else:
                user_msgs.append(m)

        payload: dict[str, Any] = {
            "model": model,
            "messages": user_msgs,
            "max_tokens": budget or 4096,
            "stream": stream,
        }
        if system_text:
            payload["system"] = [
                {"type": "text", "text": system_text,
                 "cache_control": {"type": "ephemeral"}}
            ]
        if thinking:
            payload["thinking"] = {"type": "enabled",
                                   "budget_tokens": budget or 4096}
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith("event:"):
                        continue
                    if line.startswith("data: "):
                        try:
                            ev = json.loads(line[6:])
                            if ev.get("type") == "content_block_delta":
                                delta = ev.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    tok = delta.get("text", "")
                                    if tok:
                                        yield tok
                        except (json.JSONDecodeError, KeyError):
                            continue
        except urllib.error.URLError as exc:
            raise RuntimeError(str(exc)) from exc

    async def acomplete(self, messages: list[dict], *, model: str,
                        stream: bool = True, thinking: bool = False,
                        budget: int = 4096,
                        tools: list[dict] | None = None) -> AsyncIterator[str]:
        import asyncio
        loop = asyncio.get_running_loop()
        gen  = self.complete(messages, model=model, stream=stream,
                             thinking=thinking, budget=budget, tools=tools)
        while True:
            try:
                tok = await loop.run_in_executor(None, next, gen)
                yield tok
            except StopIteration:
                break


# ── Google Gemini (OpenAI-compat endpoint) ────────────────────────────────────

class GeminiProvider:
    NAME = "gemini"
    _BASE = "https://generativelanguage.googleapis.com/v1beta/openai"

    def __init__(self, api_key: str) -> None:
        self.api_key  = api_key
        self.base_url = self._BASE
        self._models: list[str] = []

    def _headers(self) -> dict:
        return {"Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"}

    def alive(self) -> bool:
        return bool(self.api_key)

    def list_models(self) -> list[str]:
        if self._models:
            return self._models
        try:
            url = (f"https://generativelanguage.googleapis.com"
                   f"/v1beta/models?key={self.api_key}")
            with urllib.request.urlopen(url, timeout=8) as r:
                data = json.loads(r.read())
                self._models = [
                    m["name"].split("/")[-1]
                    for m in data.get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])
                ]
                return self._models
        except Exception:
            return ["gemini-1.5-pro", "gemini-1.5-flash",
                    "gemini-2.0-flash-exp"]

    def complete(self, messages: list[dict], *, model: str,
                 stream: bool = True, thinking: bool = False,
                 budget: int = 4096,
                 tools: list[dict] | None = None) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": model, "messages": messages,
            "stream": stream, "max_tokens": budget or 4096,
        }
        if tools:
            payload["tools"] = tools
        yield from _sse_stream(
            f"{self.base_url}/chat/completions",
            payload, self._headers())

    async def acomplete(self, messages: list[dict], *, model: str,
                        stream: bool = True, thinking: bool = False,
                        budget: int = 4096,
                        tools: list[dict] | None = None) -> AsyncIterator[str]:
        import asyncio
        loop = asyncio.get_running_loop()
        gen  = self.complete(messages, model=model, stream=stream,
                             thinking=thinking, budget=budget, tools=tools)
        while True:
            try:
                tok = await loop.run_in_executor(None, next, gen)
                yield tok
            except StopIteration:
                break


# ── Perplexity ────────────────────────────────────────────────────────────────

class PerplexityProvider:
    NAME = "perplexity"
    _BASE = "https://api.perplexity.ai"
    _MODELS = [
        "llama-3.1-sonar-small-128k-online",
        "llama-3.1-sonar-large-128k-online",
        "llama-3.1-sonar-huge-128k-online",
    ]

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def _headers(self) -> dict:
        return {"Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"}

    def alive(self) -> bool:
        return bool(self.api_key)

    def list_models(self) -> list[str]:
        return self._MODELS

    def complete(self, messages: list[dict], *, model: str,
                 stream: bool = True, thinking: bool = False,
                 budget: int = 4096,
                 tools: list[dict] | None = None) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": model or self._MODELS[0],
            "messages": messages,
            "stream": stream,
            "max_tokens": budget or 4096,
        }
        yield from _sse_stream(
            f"{self._BASE}/chat/completions",
            payload, self._headers())

    async def acomplete(self, messages: list[dict], *, model: str,
                        stream: bool = True, thinking: bool = False,
                        budget: int = 4096,
                        tools: list[dict] | None = None) -> AsyncIterator[str]:
        import asyncio
        loop = asyncio.get_running_loop()
        gen  = self.complete(messages, model=model, stream=stream,
                             thinking=thinking, budget=budget, tools=tools)
        while True:
            try:
                tok = await loop.run_in_executor(None, next, gen)
                yield tok
            except StopIteration:
                break


# ── Custom OpenAI-compatible provider ─────────────────────────────────────────

class CustomProvider:
    """Any OpenAI-compatible endpoint registered by the user."""

    def __init__(self, name: str, base_url: str, api_key: str = "sk-",
                 models: list[str] | None = None) -> None:
        self.NAME     = f"custom:{name}"
        self.name     = name
        self.base_url = base_url.rstrip("/")
        self.api_key  = api_key
        self._models  = models or []

    def _headers(self) -> dict:
        return {"Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"}

    def alive(self) -> bool:
        return _ping_cached(
            f"custom:{self.name}",
            f"{self.base_url}/v1/models",
            self._headers(),
        ) or _ping_cached(
            f"custom:{self.name}:health",
            f"{self.base_url}/health",
            self._headers(),
        )

    def list_models(self) -> list[str]:
        if self._models:
            return self._models
        try:
            req = urllib.request.Request(
                f"{self.base_url}/v1/models",
                headers=self._headers())
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
                self._models = [m["id"] for m in data.get("data", [])]
                return self._models
        except Exception:
            return []

    def complete(self, messages: list[dict], *, model: str,
                 stream: bool = True, thinking: bool = False,
                 budget: int = 4096,
                 tools: list[dict] | None = None) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": model, "messages": messages,
            "stream": stream, "max_tokens": budget or 4096,
            "temperature": 0.6 if thinking else 0.7,
        }
        if tools:
            payload["tools"] = tools
        yield from _sse_stream(
            f"{self.base_url}/v1/chat/completions",
            payload, self._headers())

    async def acomplete(self, messages: list[dict], *, model: str,
                        stream: bool = True, thinking: bool = False,
                        budget: int = 4096,
                        tools: list[dict] | None = None) -> AsyncIterator[str]:
        import asyncio
        loop = asyncio.get_running_loop()
        gen  = self.complete(messages, model=model, stream=stream,
                             thinking=thinking, budget=budget, tools=tools)
        while True:
            try:
                tok = await loop.run_in_executor(None, next, gen)
                yield tok
            except StopIteration:
                break


# ── Factory ───────────────────────────────────────────────────────────────────

def build_cloud_providers(store: Any) -> list[Any]:
    """Build provider instances for every configured cloud integration."""
    providers: list[Any] = []

    if key := store.get_credential("openai", "api_key"):
        base = store.get_credential("openai", "base_url") or ""
        org  = store.get_credential("openai", "org_id") or ""
        providers.append(OpenAIProvider(key, base_url=base or "https://api.openai.com",
                                        org_id=org))
        log.info("cloud_provider_registered: openai")

    if key := store.get_credential("anthropic", "api_key"):
        base = store.get_credential("anthropic", "base_url") or ""
        providers.append(AnthropicProvider(key, base_url=base))
        log.info("cloud_provider_registered: anthropic")

    if key := store.get_credential("gemini", "api_key"):
        providers.append(GeminiProvider(key))
        log.info("cloud_provider_registered: gemini")

    if key := store.get_credential("perplexity", "api_key"):
        providers.append(PerplexityProvider(key))
        log.info("cloud_provider_registered: perplexity")

    for cp in store.list_custom_providers():
        providers.append(CustomProvider(
            name=cp.get("name", "custom"),
            base_url=cp.get("base_url", ""),
            api_key=cp.get("api_key", "sk-"),
            models=cp.get("models", []),
        ))
        log.info("cloud_provider_registered: custom:%s", cp.get("name"))

    return providers
