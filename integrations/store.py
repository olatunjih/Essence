"""Persistent credential store for all Essence integrations.

Credentials are saved to <workspace>/integrations.json.
Environment variables always take priority over stored values.

Standard env-var aliases (e.g. OPENAI_API_KEY) are honoured automatically
so users don't have to re-enter keys they already have set.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.integrations.store")

# Map (integration_id, field) → standard env-var names
_ENV_ALIASES: dict[tuple[str, str], str] = {
    ("openai",     "api_key"):      "OPENAI_API_KEY",
    ("openai",     "org_id"):       "OPENAI_ORG_ID",
    ("anthropic",  "api_key"):      "ANTHROPIC_API_KEY",
    ("gemini",     "api_key"):      "GOOGLE_API_KEY",
    ("perplexity", "api_key"):      "PERPLEXITY_API_KEY",
    ("elevenlabs", "api_key"):      "ELEVENLABS_API_KEY",
    ("telegram",   "bot_token"):    "TELEGRAM_BOT_TOKEN",
    ("discord",    "bot_token"):    "DISCORD_BOT_TOKEN",
    ("discord",    "webhook_url"):  "DISCORD_WEBHOOK_URL",
    ("slack",      "bot_token"):    "SLACK_BOT_TOKEN",
    ("slack",      "webhook_url"):  "SLACK_WEBHOOK_URL",
    ("sentry",     "dsn"):          "SENTRY_DSN",
    ("prometheus", "gateway_url"):  "PROMETHEUS_GATEWAY_URL",
    ("notion",     "api_key"):      "NOTION_API_KEY",
    ("github",     "token"):        "GITHUB_TOKEN",
    ("tavily",     "api_key"):      "TAVILY_API_KEY",
    ("google_cal", "credentials"):  "GOOGLE_CALENDAR_CREDENTIALS",
}


class IntegrationStore:
    """Thread-safe JSON store for integration credentials and settings."""

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "integrations.json"
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {}
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(
                    self._path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.warning("integrations.json load failed: %s", exc)
                self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # ── credential access ─────────────────────────────────────────────────────

    def get_credential(self, integration_id: str, field: str) -> str:
        """Return credential value, honouring env-var overrides."""
        # 1. ESSENCE_<ID>_<FIELD> override (highest priority)
        env_key = f"ESSENCE_{integration_id.upper()}_{field.upper()}"
        if val := os.environ.get(env_key, ""):
            return val
        # 2. Standard env-var alias
        if alias := _ENV_ALIASES.get((integration_id, field)):
            if val := os.environ.get(alias, ""):
                return val
        # 3. Stored value
        with self._lock:
            return (self._data
                    .get(integration_id, {})
                    .get("credentials", {})
                    .get(field, ""))

    def is_configured(self, integration_id: str) -> bool:
        """True if at least one credential field is non-empty."""
        with self._lock:
            entry = self._data.get(integration_id, {})
            creds = entry.get("credentials", {})
        if any(creds.values()):
            return True
        # Check env aliases
        from essence.integrations.registry import INTEGRATION_REGISTRY
        defn = INTEGRATION_REGISTRY.get(integration_id)
        if defn is None:
            return False
        for field in defn.credential_fields:
            if self.get_credential(integration_id, field):
                return True
        return False

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def upsert(self, integration_id: str, credentials: dict[str, str],
               settings: dict[str, Any] | None = None,
               enabled: bool = True) -> None:
        """Save or update an integration's credentials and settings."""
        with self._lock:
            entry = self._data.get(integration_id, {})
            entry["credentials"] = {
                k: v for k, v in credentials.items() if v
            }
            if settings is not None:
                entry["settings"] = settings
            entry["enabled"] = enabled
            self._data[integration_id] = entry
            self._save()
        log.info("integration_saved: %s", integration_id)

    def remove(self, integration_id: str) -> bool:
        with self._lock:
            if integration_id in self._data:
                del self._data[integration_id]
                self._save()
                return True
            return False

    def get_settings(self, integration_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(
                self._data.get(integration_id, {}).get("settings", {}))

    def is_enabled(self, integration_id: str) -> bool:
        with self._lock:
            return self._data.get(integration_id, {}).get("enabled", True)

    def set_enabled(self, integration_id: str, enabled: bool) -> None:
        with self._lock:
            if integration_id not in self._data:
                self._data[integration_id] = {}
            self._data[integration_id]["enabled"] = enabled
            self._save()

    def all_integrations(self) -> dict[str, dict]:
        """Return a sanitised snapshot (credentials masked)."""
        with self._lock:
            out: dict[str, dict] = {}
            for iid, entry in self._data.items():
                creds = entry.get("credentials", {})
                masked = {k: ("***" if v else "") for k, v in creds.items()}
                out[iid] = {
                    "credentials": masked,
                    "settings": entry.get("settings", {}),
                    "enabled": entry.get("enabled", True),
                }
            return out

    # ── custom providers ──────────────────────────────────────────────────────

    def list_custom_providers(self) -> list[dict]:
        with self._lock:
            return list(
                self._data.get("_custom_providers", {})
                .get("providers", []))

    def upsert_custom_provider(self, provider: dict) -> None:
        """Add or replace a custom OpenAI-compatible provider by name."""
        with self._lock:
            bucket = self._data.setdefault(
                "_custom_providers", {"providers": []})
            providers: list = bucket.setdefault("providers", [])
            name = provider["name"]
            for i, p in enumerate(providers):
                if p["name"] == name:
                    providers[i] = provider
                    break
            else:
                providers.append(provider)
            self._save()

    def remove_custom_provider(self, name: str) -> bool:
        with self._lock:
            bucket = self._data.get("_custom_providers", {})
            providers: list = bucket.get("providers", [])
            before = len(providers)
            bucket["providers"] = [p for p in providers if p["name"] != name]
            if len(bucket["providers"]) < before:
                self._save()
                return True
            return False


# ── module-level singleton (created per workspace in create_app) ──────────────
_store: IntegrationStore | None = None


def get_store() -> IntegrationStore:
    global _store
    if _store is None:
        ws = Path(os.environ.get("ESSENCE_WORKSPACE",
                                 str(Path.home() / ".essence" / "workspace")))
        _store = IntegrationStore(ws)
    return _store


def init_store(workspace: Path) -> IntegrationStore:
    global _store
    _store = IntegrationStore(workspace)
    return _store
