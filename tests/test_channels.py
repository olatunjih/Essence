"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   Messaging channel adapters ───────────────────────────────────────

def test_telegram_adapter_unavailable_without_token():
    assert TelegramAdapter(token='').available() is False


def test_telegram_adapter_available_with_token():
    assert TelegramAdapter(token='bot123:abc').available() is True


def test_discord_adapter_unavailable_without_webhook():
    assert DiscordAdapter(webhook_url='').available() is False


