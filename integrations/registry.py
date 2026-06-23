"""Integration registry — defines every supported integration with metadata,
credential fields, and async health-check logic."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

log = logging.getLogger("essence.integrations.registry")


@dataclass
class IntegrationDef:
    id: str
    name: str
    category: str          # llm | channel | productivity | observability | voice | search
    description: str
    credential_fields: list[str]
    optional_fields: list[str] = field(default_factory=list)
    settings_fields: list[str] = field(default_factory=list)
    docs_url: str = ""
    icon: str = "🔌"
    _health_fn: Callable[["IntegrationDef", Any], Awaitable[dict]] | None = field(
        default=None, repr=False, compare=False)

    async def health_check(self, store: Any) -> dict:
        if self._health_fn:
            try:
                return await self._health_fn(self, store)
            except Exception as exc:
                return {"ok": False, "error": str(exc)[:120]}
        return {"ok": store.is_configured(self.id), "error": None}


# ── Health-check helpers ──────────────────────────────────────────────────────

async def _hc_openai(defn: IntegrationDef, store: Any) -> dict:
    api_key = store.get_credential("openai", "api_key")
    if not api_key:
        return {"ok": False, "error": "api_key not set"}
    try:
        import urllib.request, json as _json
        base = store.get_credential("openai", "base_url") or "https://api.openai.com"
        req = urllib.request.Request(
            f"{base.rstrip('/')}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
            models = [m["id"] for m in data.get("data", [])]
            return {"ok": True, "models": models[:20]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


async def _hc_anthropic(defn: IntegrationDef, store: Any) -> dict:
    api_key = store.get_credential("anthropic", "api_key")
    if not api_key:
        return {"ok": False, "error": "api_key not set"}
    try:
        import urllib.request, json as _json
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
            models = [m["id"] for m in data.get("data", [])]
            return {"ok": True, "models": models}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


async def _hc_gemini(defn: IntegrationDef, store: Any) -> dict:
    api_key = store.get_credential("gemini", "api_key")
    if not api_key:
        return {"ok": False, "error": "api_key not set"}
    try:
        import urllib.request, json as _json
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        with urllib.request.urlopen(url, timeout=8) as r:
            data = _json.loads(r.read())
            models = [m["name"].split("/")[-1] for m in data.get("models", [])]
            return {"ok": True, "models": models}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


async def _hc_perplexity(defn: IntegrationDef, store: Any) -> dict:
    api_key = store.get_credential("perplexity", "api_key")
    if not api_key:
        return {"ok": False, "error": "api_key not set"}
    return {"ok": True, "models": [
        "llama-3.1-sonar-small-128k-online",
        "llama-3.1-sonar-large-128k-online",
        "llama-3.1-sonar-huge-128k-online",
    ]}


async def _hc_elevenlabs(defn: IntegrationDef, store: Any) -> dict:
    api_key = store.get_credential("elevenlabs", "api_key")
    if not api_key:
        return {"ok": False, "error": "api_key not set"}
    try:
        import urllib.request, json as _json
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": api_key},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
            voices = [v["name"] for v in data.get("voices", [])]
            return {"ok": True, "voices": voices[:10]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


async def _hc_telegram(defn: IntegrationDef, store: Any) -> dict:
    token = store.get_credential("telegram", "bot_token")
    if not token:
        return {"ok": False, "error": "bot_token not set"}
    try:
        import urllib.request, json as _json
        url = f"https://api.telegram.org/bot{token}/getMe"
        with urllib.request.urlopen(url, timeout=8) as r:
            data = _json.loads(r.read())
            if data.get("ok"):
                bot = data["result"]
                return {"ok": True,
                        "bot": f"@{bot.get('username')} ({bot.get('first_name')})"}
            return {"ok": False, "error": data.get("description", "unknown")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


async def _hc_discord(defn: IntegrationDef, store: Any) -> dict:
    token = store.get_credential("discord", "bot_token")
    webhook = store.get_credential("discord", "webhook_url")
    if not token and not webhook:
        return {"ok": False, "error": "bot_token or webhook_url required"}
    if webhook:
        try:
            import urllib.request, json as _json
            with urllib.request.urlopen(webhook, timeout=8) as r:
                data = _json.loads(r.read())
                return {"ok": True, "channel": data.get("name", "unknown")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:120]}
    return {"ok": True, "detail": "bot_token set (webhook recommended for outbound)"}


async def _hc_slack(defn: IntegrationDef, store: Any) -> dict:
    token = store.get_credential("slack", "bot_token")
    if not token:
        return {"ok": False, "error": "bot_token not set"}
    try:
        import urllib.request, json as _json
        req = urllib.request.Request(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
            if data.get("ok"):
                return {"ok": True, "team": data.get("team"),
                        "user": data.get("user")}
            return {"ok": False, "error": data.get("error", "unknown")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


async def _hc_notion(defn: IntegrationDef, store: Any) -> dict:
    api_key = store.get_credential("notion", "api_key")
    if not api_key:
        return {"ok": False, "error": "api_key not set"}
    try:
        import urllib.request, json as _json
        req = urllib.request.Request(
            "https://api.notion.com/v1/users/me",
            headers={"Authorization": f"Bearer {api_key}",
                     "Notion-Version": "2022-06-28"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
            return {"ok": True, "user": data.get("name", data.get("id"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


async def _hc_github(defn: IntegrationDef, store: Any) -> dict:
    token = store.get_credential("github", "token")
    if not token:
        return {"ok": False, "error": "token not set"}
    try:
        import urllib.request, json as _json
        req = urllib.request.Request(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
            return {"ok": True, "user": data.get("login")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


async def _hc_sentry(defn: IntegrationDef, store: Any) -> dict:
    dsn = store.get_credential("sentry", "dsn")
    if not dsn:
        return {"ok": False, "error": "dsn not set"}
    return {"ok": True, "detail": "DSN configured (SDK initialises on startup)"}


async def _hc_prometheus(defn: IntegrationDef, store: Any) -> dict:
    gw = store.get_credential("prometheus", "gateway_url")
    if not gw:
        return {"ok": False, "error": "gateway_url not set"}
    try:
        import urllib.request
        with urllib.request.urlopen(gw.rstrip("/") + "/-/healthy", timeout=5) as r:
            return {"ok": r.status < 400, "url": gw}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


async def _hc_tavily(defn: IntegrationDef, store: Any) -> dict:
    key = store.get_credential("tavily", "api_key")
    if not key:
        return {"ok": False, "error": "api_key not set"}
    return {"ok": True, "detail": "API key set"}


async def _hc_google_cal(defn: IntegrationDef, store: Any) -> dict:
    creds = store.get_credential("google_cal", "credentials")
    if not creds:
        return {"ok": False, "error": "credentials JSON not set"}
    return {"ok": True, "detail": "credentials configured"}


async def _hc_obsidian(defn: IntegrationDef, store: Any) -> dict:
    from pathlib import Path as _P
    vault = store.get_credential("obsidian", "vault_path")
    if not vault:
        return {"ok": False, "error": "vault_path not set"}
    p = _P(vault)
    if p.exists():
        notes = sum(1 for _ in p.rglob("*.md"))
        return {"ok": True, "notes": notes, "path": str(p)}
    return {"ok": False, "error": f"path not found: {vault}"}


async def _hc_custom_provider(defn: IntegrationDef, store: Any) -> dict:
    providers = store.list_custom_providers()
    return {"ok": len(providers) > 0,
            "count": len(providers),
            "providers": [p["name"] for p in providers]}


# ── Registry definition ───────────────────────────────────────────────────────

_DEFS: list[IntegrationDef] = [
    # ── LLM providers ─────────────────────────────────────────────────────────
    IntegrationDef(
        id="openai", name="OpenAI", category="llm",
        icon="🟢",
        description="GPT-4o, o1, o3 — cloud reasoning and coding models.",
        credential_fields=["api_key"],
        optional_fields=["org_id", "base_url"],
        settings_fields=["default_model", "max_tokens"],
        docs_url="https://platform.openai.com/api-keys",
        _health_fn=_hc_openai,
    ),
    IntegrationDef(
        id="anthropic", name="Anthropic / Claude", category="llm",
        icon="🟠",
        description="Claude 3.5 / 4 Sonnet & Opus — best for long context and planning.",
        credential_fields=["api_key"],
        optional_fields=["base_url"],
        settings_fields=["default_model", "prompt_caching"],
        docs_url="https://console.anthropic.com/keys",
        _health_fn=_hc_anthropic,
    ),
    IntegrationDef(
        id="gemini", name="Google Gemini", category="llm",
        icon="🔵",
        description="Gemini 1.5 Pro / Flash — multimodal, 1M token context.",
        credential_fields=["api_key"],
        settings_fields=["default_model"],
        docs_url="https://aistudio.google.com/app/apikey",
        _health_fn=_hc_gemini,
    ),
    IntegrationDef(
        id="perplexity", name="Perplexity", category="llm",
        icon="🌐",
        description="Sonar models — real-time web-grounded answers with citations.",
        credential_fields=["api_key"],
        settings_fields=["default_model"],
        docs_url="https://www.perplexity.ai/settings/api",
        _health_fn=_hc_perplexity,
    ),
    IntegrationDef(
        id="custom_provider", name="Custom Provider", category="llm",
        icon="⚙️",
        description="Any OpenAI-compatible endpoint (vLLM, LM Studio, OpenRouter, etc.).",
        credential_fields=[],
        settings_fields=["providers"],
        docs_url="",
        _health_fn=_hc_custom_provider,
    ),
    # ── Messaging channels ─────────────────────────────────────────────────────
    IntegrationDef(
        id="telegram", name="Telegram", category="channel",
        icon="✈️",
        description="Send and receive messages via a Telegram bot.",
        credential_fields=["bot_token"],
        optional_fields=["default_chat_id"],
        docs_url="https://core.telegram.org/bots#botfather",
        _health_fn=_hc_telegram,
    ),
    IntegrationDef(
        id="discord", name="Discord", category="channel",
        icon="🎮",
        description="Post to Discord channels via webhook or bot.",
        credential_fields=["webhook_url"],
        optional_fields=["bot_token", "guild_id"],
        docs_url="https://discord.com/developers/applications",
        _health_fn=_hc_discord,
    ),
    IntegrationDef(
        id="slack", name="Slack", category="channel",
        icon="💬",
        description="Send Slack messages via bot token or incoming webhook.",
        credential_fields=["bot_token"],
        optional_fields=["webhook_url", "default_channel"],
        docs_url="https://api.slack.com/apps",
        _health_fn=_hc_slack,
    ),
    # ── Productivity ───────────────────────────────────────────────────────────
    IntegrationDef(
        id="notion", name="Notion", category="productivity",
        icon="📝",
        description="Read/write Notion pages and databases for knowledge management.",
        credential_fields=["api_key"],
        optional_fields=["database_id"],
        docs_url="https://www.notion.so/my-integrations",
        _health_fn=_hc_notion,
    ),
    IntegrationDef(
        id="github", name="GitHub", category="productivity",
        icon="🐙",
        description="Open issues, review PRs, and run actions via GitHub API.",
        credential_fields=["token"],
        optional_fields=["default_repo"],
        docs_url="https://github.com/settings/tokens",
        _health_fn=_hc_github,
    ),
    IntegrationDef(
        id="google_cal", name="Google Calendar", category="productivity",
        icon="📅",
        description="Read your schedule for daily briefings and reminders.",
        credential_fields=["credentials"],
        optional_fields=["calendar_id"],
        docs_url="https://console.cloud.google.com/apis/credentials",
        _health_fn=_hc_google_cal,
    ),
    IntegrationDef(
        id="obsidian", name="Obsidian Vault", category="productivity",
        icon="🔮",
        description="Index your local Obsidian vault into the knowledge graph.",
        credential_fields=["vault_path"],
        docs_url="https://obsidian.md",
        _health_fn=_hc_obsidian,
    ),
    # ── Observability ──────────────────────────────────────────────────────────
    IntegrationDef(
        id="sentry", name="Sentry", category="observability",
        icon="🛡️",
        description="Automatic error tracking, performance traces, and alerts.",
        credential_fields=["dsn"],
        optional_fields=["environment", "traces_sample_rate"],
        docs_url="https://sentry.io/settings/projects/",
        _health_fn=_hc_sentry,
    ),
    IntegrationDef(
        id="prometheus", name="Prometheus / Grafana", category="observability",
        icon="📈",
        description="Push metrics to a Prometheus PushGateway for Grafana dashboards.",
        credential_fields=["gateway_url"],
        optional_fields=["job_name", "username", "password"],
        docs_url="https://prometheus.io/docs/instrumenting/pushing/",
        _health_fn=_hc_prometheus,
    ),
    # ── Voice ──────────────────────────────────────────────────────────────────
    IntegrationDef(
        id="elevenlabs", name="ElevenLabs", category="voice",
        icon="🔊",
        description="High-quality neural TTS — replaces the local kokoro-onnx engine.",
        credential_fields=["api_key"],
        optional_fields=["voice_id", "model_id"],
        docs_url="https://elevenlabs.io/app/settings/api-keys",
        _health_fn=_hc_elevenlabs,
    ),
    # ── Search ─────────────────────────────────────────────────────────────────
    IntegrationDef(
        id="tavily", name="Tavily Search", category="search",
        icon="🔍",
        description="Real-time web search API — replaces DuckDuckGo for research tasks.",
        credential_fields=["api_key"],
        settings_fields=["search_depth", "max_results"],
        docs_url="https://tavily.com/#api",
        _health_fn=_hc_tavily,
    ),
]

INTEGRATION_REGISTRY: dict[str, IntegrationDef] = {d.id: d for d in _DEFS}


def get_by_category(category: str) -> list[IntegrationDef]:
    return [d for d in _DEFS if d.category == category]


async def health_check_all(store: Any,
                           ids: list[str] | None = None) -> dict[str, dict]:
    """Run all (or selected) health checks concurrently."""
    targets = [d for d in _DEFS if ids is None or d.id in ids]
    results = await asyncio.gather(
        *[d.health_check(store) for d in targets],
        return_exceptions=True,
    )
    out: dict[str, dict] = {}
    for defn, res in zip(targets, results):
        if isinstance(res, Exception):
            out[defn.id] = {"ok": False, "error": str(res)[:120]}
        else:
            out[defn.id] = res
    return out
