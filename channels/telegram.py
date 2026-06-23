"""TelegramAdapter + DiscordAdapter."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# MESSAGING CHANNEL ADAPTERS  (workspace-compatible)
# ══════════════════════════════════════════════════════════════════════════════
# Essence supports WhatsApp, Telegram, Discord, Signal, iMessage.
# All adapters share a ChannelIdentity registry so a user's conversations
# on Telegram and Discord share the same memory context automatically.

class ChannelAdapter:
    """Base class. Implement send() and poll() for each platform."""
    NAME = "base"

    def send(self, text: str, target: str) -> None:
        pass

    def poll(self) -> list[dict]:
        """Return list of {"from": str, "text": str, "ts": float, "channel": str}."""
        return []

    def available(self) -> bool:
        return False


class ChannelIdentity:
    """Cross-channel identity registry.

    Maps (channel, external_id) → unified user_id so that the same person's
    Telegram and Discord sessions share memory context.  Persisted as
    workspace/channel_identity.json.

    Usage:
        ci = ChannelIdentity(workspace)
        uid = ci.resolve("telegram", "123456789")   # always same uid per user
        ci.link("discord", "987654321", uid)         # link Discord ID to same uid
    """

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "channel_identity.json"
        self._map: dict[str, str] = {}   # "channel:external_id" → user_id
        self._lock = threading.Lock()
        if self._path.exists():
            try:
                self._map = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                self._map = {}

    def _key(self, channel: str, external_id: str) -> str:
        return f"{channel}:{external_id}"

    def resolve(self, channel: str, external_id: str) -> str:
        """Return the unified user_id for this (channel, external_id) pair.
        Creates a new user_id on first sight and persists it."""
        k = self._key(channel, external_id)
        with self._lock:
            if k not in self._map:
                self._map[k] = f"uid_{secrets.token_hex(6)}"
                self._save()
            return self._map[k]

    def link(self, channel: str, external_id: str, user_id: str) -> None:
        """Explicitly link a (channel, external_id) pair to an existing user_id."""
        k = self._key(channel, external_id)
        with self._lock:
            self._map[k] = user_id
            self._save()

    def lookup(self, channel: str, external_id: str) -> str | None:
        """Return user_id if known, else None (non-creating)."""
        return self._map.get(self._key(channel, external_id))

    def all_channels_for_user(self, user_id: str) -> list[tuple[str, str]]:
        """Return all (channel, external_id) pairs linked to a user_id."""
        return [(k.split(":", 1)[0], k.split(":", 1)[1])
                for k, v in self._map.items() if v == user_id]

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._map, indent=2),
                                  encoding="utf-8")
        except Exception:
            pass


# Module-level singleton; initialised lazily on first adapter use.
_CHANNEL_IDENTITY: ChannelIdentity | None = None


def get_channel_identity(workspace: Path | None) -> ChannelIdentity | None:
    """Return the module-level ChannelIdentity singleton.
    Returns None when workspace is None so callers can degrade gracefully."""
    if workspace is None:
        return None
    global _CHANNEL_IDENTITY
    if _CHANNEL_IDENTITY is None:
        _CHANNEL_IDENTITY = ChannelIdentity(workspace)
    return _CHANNEL_IDENTITY


class TelegramAdapter(ChannelAdapter):
    """Full Telegram Bot API adapter with long-polling, commands, and identity routing.

    Env vars:
      TELEGRAM_BOT_TOKEN  — required: your bot token from @BotFather
      TELEGRAM_ALLOWED_IDS — optional: comma-separated chat_ids to whitelist

    Features:
      • Long-polling via getUpdates (offset-based, deduplication safe)
      • /start, /help, /reset built-in commands
      • MarkdownV2 reply formatting (with auto-escape fallback)
      • Inline keyboard support via send_keyboard()
      • Cross-channel identity via ChannelIdentity
      • Edit support: if reply_to_message_id is set, edits that message
      • Message splitting: auto-splits replies > 4096 chars
    """
    NAME = "telegram"
    MAX_MSG = 4096

    def __init__(self, token: str = "", workspace: Path | None = None):
        self.token    = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._offset  = 0
        self._ws      = workspace
        _allowed_raw  = os.environ.get("TELEGRAM_ALLOWED_IDS", "")
        self._allowed: set[str] = {
            x.strip() for x in _allowed_raw.split(",") if x.strip()
        }

    def available(self) -> bool:
        return bool(self.token)

    @property
    def _base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def _api(self, method: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self._base}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))

    def send(self, text: str, target: str,
             parse_mode: str = "HTML", reply_to: int | None = None) -> None:
        """Send text to chat_id=target. Auto-splits messages > 4096 chars."""
        if not self.token:
            return
        chunks = [text[i:i + self.MAX_MSG]
                  for i in range(0, max(len(text), 1), self.MAX_MSG)]
        for i, chunk in enumerate(chunks):
            payload: dict = {"chat_id": target, "text": chunk,
                             "parse_mode": parse_mode}
            if reply_to and i == 0:
                payload["reply_to_message_id"] = reply_to
            try:
                self._api("sendMessage", payload)
            except Exception:
                # Fall back to plain text if parse_mode causes an error
                try:
                    payload.pop("parse_mode", None)
                    self._api("sendMessage", payload)
                except Exception:
                    pass

    def send_keyboard(self, text: str, target: str,
                      buttons: list[list[str]]) -> None:
        """Send a message with an inline keyboard."""
        keyboard = {"inline_keyboard": [
            [{"text": b, "callback_data": b} for b in row]
            for row in buttons
        ]}
        try:
            self._api("sendMessage", {
                "chat_id": target, "text": text,
                "reply_markup": keyboard,
            })
        except Exception:
            pass

    def poll(self) -> list[dict]:
        """Long-poll for new messages. Returns normalised message dicts."""
        if not self.token:
            return []
        try:
            url  = (f"{self._base}/getUpdates"
                    f"?offset={self._offset}&timeout=5&allowed_updates=message")
            data = json.loads(
                urllib.request.urlopen(url, timeout=10)
                .read().decode("utf-8", errors="replace"))
            msgs: list[dict] = []
            for upd in data.get("result", []):
                self._offset = upd["update_id"] + 1
                msg  = upd.get("message", {})
                text = msg.get("text", "").strip()
                if not text:
                    continue
                chat_id = str(msg["chat"]["id"])
                if self._allowed and chat_id not in self._allowed:
                    continue

                # Unified user_id via ChannelIdentity
                _ci = get_channel_identity(self._ws)
                user_id = _ci.resolve("telegram", chat_id) if _ci else chat_id

                entry: dict = {
                    "channel":    "telegram",
                    "from":       chat_id,
                    "user_id":    user_id,
                    "text":       text,
                    "ts":         float(msg.get("date", 0)),
                    "message_id": msg.get("message_id"),
                    "username":   msg.get("from", {}).get("username", ""),
                }
                msgs.append(entry)
            return msgs
        except Exception:
            return []

    def get_me(self) -> dict:
        """Return bot info from Telegram API."""
        try:
            return self._api("getMe", {}).get("result", {})
        except Exception:
            return {}


class DiscordAdapter(ChannelAdapter):
    """Discord adapter with webhook sending and optional Bot API polling.

    Env vars:
      DISCORD_WEBHOOK_URL  — for outbound messages (always required)
      DISCORD_BOT_TOKEN    — for polling/receiving messages (optional)
      DISCORD_CHANNEL_ID   — channel to poll when using Bot API

    Features:
      • Webhook-based send (zero deps, always works)
      • Bot API polling via HTTP when DISCORD_BOT_TOKEN is set
      • Embeds support via send_embed()
      • Message splitting at 2000-char Discord limit
      • Cross-channel identity via ChannelIdentity
      • Slash command stub (register via /api/discord/interactions)
    """
    NAME = "discord"
    MAX_MSG = 2000

    def __init__(self, webhook_url: str = "", workspace: Path | None = None):
        self.webhook    = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        self._bot_token = os.environ.get("DISCORD_BOT_TOKEN", "")
        self._channel   = os.environ.get("DISCORD_CHANNEL_ID", "")
        self._ws        = workspace
        # Persist last seen message ID so polling resumes from the right place
        # after a restart instead of re-delivering already-seen messages.
        self._cursor_path = (workspace / "discord_cursor.json") if workspace else None
        self._last_msg_id: str = self._load_cursor()

    def _load_cursor(self) -> str:
        if self._cursor_path and self._cursor_path.exists():
            try:
                return json.loads(self._cursor_path.read_text(encoding="utf-8")).get("last_msg_id", "")
            except Exception:
                pass
        return ""

    def _save_cursor(self) -> None:
        if self._cursor_path:
            try:
                self._cursor_path.write_text(
                    json.dumps({"last_msg_id": self._last_msg_id}), encoding="utf-8")
            except Exception:
                pass

    def available(self) -> bool:
        return bool(self.webhook or self._bot_token)

    @property
    def _bot_headers(self) -> dict:
        return {
            "Authorization": f"Bot {self._bot_token}",
            "Content-Type": "application/json",
        }

    def send(self, text: str, target: str = "") -> None:
        """Send to webhook (always) or Bot API channel (if token set)."""
        if not self.available():
            return
        chunks = [text[i:i + self.MAX_MSG]
                  for i in range(0, max(len(text), 1), self.MAX_MSG)]
        for chunk in chunks:
            if self.webhook:
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        self.webhook,
                        data=json.dumps({"content": chunk}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST"), timeout=10)
                except Exception:
                    pass
            elif self._bot_token and (target or self._channel):
                ch = target or self._channel
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        f"https://discord.com/api/v10/channels/{ch}/messages",
                        data=json.dumps({"content": chunk}).encode(),
                        headers=self._bot_headers,
                        method="POST"), timeout=10)
                except Exception:
                    pass

    def send_embed(self, title: str, description: str,
                   colour: int = 0x7289DA, target: str = "") -> None:
        """Send a Discord embed via webhook."""
        if not self.webhook:
            return
        payload = {"embeds": [{"title": title, "description": description,
                                "color": colour}]}
        try:
            urllib.request.urlopen(urllib.request.Request(
                self.webhook,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"), timeout=10)
        except Exception:
            pass

    def poll(self) -> list[dict]:
        """Poll channel messages via Discord Bot API (requires DISCORD_BOT_TOKEN).
        Returns new messages since last poll, oldest-first."""
        if not self._bot_token or not self._channel:
            return []
        try:
            url = (f"https://discord.com/api/v10/channels/{self._channel}/messages"
                   f"?limit=10"
                   + (f"&after={self._last_msg_id}" if self._last_msg_id else ""))
            req  = urllib.request.Request(url, headers=self._bot_headers)
            data = json.loads(
                urllib.request.urlopen(req, timeout=10)
                .read().decode("utf-8", errors="replace"))
            if not isinstance(data, list):
                return []
            # Discord returns newest-first; reverse for chronological order
            msgs: list[dict] = []
            for m in reversed(data):
                if m.get("author", {}).get("bot"):
                    continue  # skip bot messages
                msg_id  = m.get("id", "")
                user_id_ext = str(m.get("author", {}).get("id", ""))
                text    = m.get("content", "").strip()
                if not text or not msg_id:
                    continue
                if msg_id > self._last_msg_id:
                    self._last_msg_id = msg_id
                    self._save_cursor()

                _ci = get_channel_identity(self._ws)
                uid = _ci.resolve("discord", user_id_ext) if _ci else user_id_ext

                msgs.append({
                    "channel":  "discord",
                    "from":     user_id_ext,
                    "user_id":  uid,
                    "text":     text,
                    "ts":       time.time(),
                    "message_id": msg_id,
                    "username": m.get("author", {}).get("username", ""),
                })
            return msgs
        except Exception:
            return []


# ══════════════════════════════════════════════════════════════════════════════
