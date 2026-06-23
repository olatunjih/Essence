"""Matrix, Teams, LINE, iMessage adapters."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *
from essence.channels.telegram import ChannelAdapter  # noqa: F401  [auto-fix: missing import]  # noqa: F401,F403

# EXTENDED CHANNEL ADAPTERS II  (Matrix · Teams · LINE · iMessage)
# ══════════════════════════════════════════════════════════════════════════════
# Env-gated; zero mandatory imports. All follow the ChannelAdapter interface.

class MatrixAdapter(ChannelAdapter):
    """
    Matrix / Element homeserver adapter using the Client-Server API v3.

    Env vars:
      MATRIX_HOMESERVER    — required: e.g. https://matrix.org
      MATRIX_ACCESS_TOKEN  — required: bot access token (get via login or
                             Element → Settings → Help & About → Access Token)
      MATRIX_ROOM_ID       — default room for send() when target is empty
                             format: !roomid:homeserver  (e.g. !abc:matrix.org)

    Features:
      • send()   — POST /rooms/{roomId}/send/m.room.message
      • poll()   — GET  /sync with since= token (long-poll timeout 5s)
      • Deduplication via event_id cursor persisted to workspace
      • Cross-channel identity via ChannelIdentity
      • Message splitting at 50 000-char Matrix limit
    """
    NAME = "matrix"
    MAX_MSG = 50_000

    def __init__(self, homeserver: str = "", token: str = "",
                 room_id: str = "", workspace: Path | None = None) -> None:
        self._hs     = (homeserver or os.environ.get("MATRIX_HOMESERVER", "")).rstrip("/")
        self._token  = token   or os.environ.get("MATRIX_ACCESS_TOKEN", "")
        self._room   = room_id or os.environ.get("MATRIX_ROOM_ID", "")
        self._ws     = workspace
        self._since  = ""   # /sync next_batch token
        # Load persisted since-token
        self._cursor_path = (workspace / "matrix_cursor.json") if workspace else None
        if self._cursor_path and self._cursor_path.exists():
            try:
                self._since = json.loads(
                    self._cursor_path.read_text(encoding="utf-8")).get("since", "")
            except Exception:
                pass

    def available(self) -> bool:
        return bool(self._hs and self._token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}",
                "Content-Type":  "application/json"}

    def _client_url(self, path: str) -> str:
        return f"{self._hs}/_matrix/client/v3{path}"

    def send(self, text: str, target: str = "") -> None:
        """Send a plain-text m.room.message to target room (falls back to _room)."""
        if not self.available():
            return
        room = target or self._room
        if not room:
            log.warning("matrix_send_no_room")
            return
        txn = secrets.token_hex(8)
        url = self._client_url(f"/rooms/{urllib.parse.quote(room, safe='')}"
                               f"/send/m.room.message/{txn}")
        chunks = [text[i:i + self.MAX_MSG]
                  for i in range(0, max(len(text), 1), self.MAX_MSG)]
        for chunk in chunks:
            body = json.dumps({"msgtype": "m.text", "body": chunk}).encode()
            req  = urllib.request.Request(url, data=body,
                                          headers=self._headers(), method="PUT")
            try:
                urllib.request.urlopen(req, timeout=12)
            except Exception as e:
                log.warning("matrix_send_error", extra={"error": str(e)[:80]})

    def poll(self) -> list[dict]:
        """Long-poll for new room messages via /sync."""
        if not self.available():
            return []
        params = urllib.parse.urlencode({
            "timeout": 5000,   # 5 s long-poll
            "filter":  json.dumps({"room": {"timeline": {"limit": 20}}}),
            **({"since": self._since} if self._since else {}),
        })
        url = self._client_url(f"/sync?{params}")
        req = urllib.request.Request(url, headers=self._headers())
        try:
            data = json.loads(
                urllib.request.urlopen(req, timeout=12)
                .read().decode("utf-8", errors="replace"))
        except Exception:
            return []

        next_batch = data.get("next_batch", self._since)
        if next_batch != self._since:
            self._since = next_batch
            if self._cursor_path:
                try:
                    self._cursor_path.write_text(
                        json.dumps({"since": self._since}), encoding="utf-8")
                except Exception:
                    pass

        msgs: list[dict] = []
        rooms_joined = data.get("rooms", {}).get("join", {})
        for room_id, room_data in rooms_joined.items():
            for event in room_data.get("timeline", {}).get("events", []):
                if event.get("type") != "m.room.message":
                    continue
                content = event.get("content", {})
                if content.get("msgtype") != "m.text":
                    continue
                sender = event.get("sender", "")
                body   = content.get("body", "").strip()
                if not body:
                    continue
                _ci = get_channel_identity(self._ws)
                uid = _ci.resolve("matrix", sender) if _ci else sender
                msgs.append({
                    "channel":  "matrix",
                    "from":     sender,
                    "user_id":  uid,
                    "text":     body,
                    "ts":       event.get("origin_server_ts", time.time() * 1000) / 1000,
                    "room_id":  room_id,
                })
        return msgs


class TeamsAdapter(ChannelAdapter):
    """
    Microsoft Teams adapter.

    Two modes depending on available credentials:

    OUTBOUND only — Incoming Webhook:
      TEAMS_WEBHOOK_URL   — paste the webhook URL from the Teams channel connector.
      No polling; use this for notifications/alerts from Essence → a Teams channel.

    FULL duplex — Bot Framework (requires Azure app registration):
      TEAMS_BOT_ID        — Azure app (client) ID
      TEAMS_BOT_PASSWORD  — Azure app client secret
      Configure the bot's messaging endpoint to POST to
        /api/webhooks/receive?source=teams  on the Essence server.
      The poll() method here is a no-op in Bot Framework mode; incoming messages
      arrive via that webhook endpoint.

    Env vars:
      TEAMS_WEBHOOK_URL   — incoming webhook URL (outbound-only mode)
      TEAMS_BOT_ID        — Bot Framework app ID
      TEAMS_BOT_PASSWORD  — Bot Framework app secret
    """
    NAME = "teams"
    _GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    _BOT_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"

    def __init__(self, webhook_url: str = "", bot_id: str = "",
                 bot_password: str = "") -> None:
        self._webhook  = webhook_url  or os.environ.get("TEAMS_WEBHOOK_URL", "")
        self._bot_id   = bot_id       or os.environ.get("TEAMS_BOT_ID", "")
        self._bot_pass = bot_password or os.environ.get("TEAMS_BOT_PASSWORD", "")
        self._bot_token: str = ""
        self._bot_token_exp: float = 0.0

    def available(self) -> bool:
        return bool(self._webhook or self._bot_id)

    def _get_bot_token(self) -> str:
        """Fetch/cache an OAuth2 bot token for the Bot Framework."""
        if self._bot_token and time.time() < self._bot_token_exp - 60:
            return self._bot_token
        if not (self._bot_id and self._bot_pass):
            return ""
        body = urllib.parse.urlencode({
            "grant_type":    "client_credentials",
            "client_id":     self._bot_id,
            "client_secret": self._bot_pass,
            "scope":         "https://api.botframework.com/.default",
        }).encode()
        req = urllib.request.Request(
            self._BOT_TOKEN_URL, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST")
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
            self._bot_token     = resp.get("access_token", "")
            self._bot_token_exp = time.time() + int(resp.get("expires_in", 3600))
            return self._bot_token
        except Exception as e:
            log.warning("teams_token_error", extra={"error": str(e)[:80]})
            return ""

    def send(self, text: str, target: str = "") -> None:
        """
        Send a message.  Uses webhook when available; Bot Framework reply otherwise.
        target = conversation serviceUrl+conversation_id for bot-initiated messages.
        """
        if not self.available():
            return
        if self._webhook:
            # Adaptive Card (simple text via MessageCard for broad compat)
            payload = json.dumps({
                "@type":      "MessageCard",
                "@context":   "https://schema.org/extensions",
                "text":       text[:28_000],
            }).encode()
            req = urllib.request.Request(
                self._webhook, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST")
            try:
                urllib.request.urlopen(req, timeout=12)
            except Exception as e:
                log.warning("teams_webhook_error", extra={"error": str(e)[:80]})
        elif target and self._bot_id:
            # Bot Framework: POST to target serviceUrl/v3/conversations/{id}/activities
            tok = self._get_bot_token()
            if not tok:
                return
            service_url, conv_id = (target.split("|", 1) + [""])[:2]
            url = f"{service_url.rstrip('/')}/v3/conversations/{conv_id}/activities"
            body = json.dumps({"type": "message", "text": text[:28_000]}).encode()
            req  = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {tok}"},
                method="POST")
            try:
                urllib.request.urlopen(req, timeout=12)
            except Exception as e:
                log.warning("teams_bot_send_error", extra={"error": str(e)[:80]})

    def poll(self) -> list[dict]:
        """
        Teams Bot Framework is webhook-driven; messages arrive via POST.
        Wire /api/webhooks/receive?source=teams on the Essence server.
        poll() is a no-op here.
        """
        return []


class LineAdapter(ChannelAdapter):
    """
    LINE Messaging API adapter.

    Env vars:
      LINE_CHANNEL_TOKEN   — required: channel access token
                             (LINE Developers Console → Messaging API → Channel access token)
      LINE_CHANNEL_SECRET  — required for webhook signature verification
      LINE_DEFAULT_USER    — optional: default userId for send() when target is empty

    Features:
      • send()   — POST https://api.line.me/v2/bot/message/push
      • reply()  — POST /reply (use when responding inside a webhook event)
      • poll()   — no-op; messages arrive via POST webhook.
                   Wire /api/webhooks/receive?source=line on the Essence server.
      • HMAC-SHA256 webhook signature verification via verify_signature()
    """
    NAME = "line"
    _API_BASE = "https://api.line.me/v2/bot"

    def __init__(self, channel_token: str = "", channel_secret: str = "",
                 default_user: str = "") -> None:
        self._token   = channel_token  or os.environ.get("LINE_CHANNEL_TOKEN", "")
        self._secret  = channel_secret or os.environ.get("LINE_CHANNEL_SECRET", "")
        self._default = default_user   or os.environ.get("LINE_DEFAULT_USER", "")

    def available(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}",
                "Content-Type":  "application/json"}

    def send(self, text: str, target: str = "") -> None:
        """Push a text message to a userId / groupId / roomId."""
        uid = target or self._default
        if not uid or not self._token:
            return
        # LINE max 5000 chars per text message
        chunks = [text[i:i + 5000] for i in range(0, max(len(text), 1), 5000)]
        messages = [{"type": "text", "text": c} for c in chunks[:5]]  # API limit: 5 messages
        body = json.dumps({"to": uid, "messages": messages}).encode()
        req  = urllib.request.Request(
            f"{self._API_BASE}/message/push", data=body,
            headers=self._headers(), method="POST")
        try:
            urllib.request.urlopen(req, timeout=12)
        except Exception as e:
            log.warning("line_send_error", extra={"error": str(e)[:80]})

    def reply(self, reply_token: str, text: str) -> None:
        """Reply to a webhook event using its replyToken (faster, no cost quota)."""
        if not (reply_token and self._token):
            return
        body = json.dumps({
            "replyToken": reply_token,
            "messages":   [{"type": "text", "text": text[:5000]}],
        }).encode()
        req = urllib.request.Request(
            f"{self._API_BASE}/message/reply", data=body,
            headers=self._headers(), method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log.warning("line_reply_error", extra={"error": str(e)[:80]})

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """Verify the X-Line-Signature HMAC-SHA256 header from LINE webhooks."""
        import hmac
        if not self._secret:
            return True  # can't verify without secret; allow through
        expected = base64.b64encode(
            hmac.new(self._secret.encode("utf-8"), body, "sha256").digest()
        ).decode("utf-8")
        return secrets.compare_digest(expected, signature)

    def poll(self) -> list[dict]:
        """LINE uses push webhooks; poll() is a no-op. See verify_signature()."""
        return []


class IMessageAdapter(ChannelAdapter):
    """
    iMessage adapter (macOS only, requires macOS 12+ with Messages.app).

    Uses AppleScript via osascript to send messages through the local
    Messages.app.  Receiving is done by monitoring the local chat database
    (~/Library/Messages/chat.db) for new unread messages.

    Env vars:
      IMESSAGE_HANDLE   — the iMessage handle to send FROM / poll for replies
                          (your Apple ID email or phone, e.g. user@icloud.com)
      IMESSAGE_POLL_DB  — set to "0" to disable DB polling (send-only mode)

    Limitations:
      • macOS only; gracefully reports unavailable on Linux/Windows.
      • Messages.app must be running and signed in.
      • DB polling reads chat.db directly; Messages.app must not have
        Full Disk Access blocked for the process.
      • No group message support (DMs only).
    """
    NAME = "imessage"

    def __init__(self, handle: str = "") -> None:
        self._handle    = handle or os.environ.get("IMESSAGE_HANDLE", "")
        self._poll_db   = os.environ.get("IMESSAGE_POLL_DB", "1") != "0"
        self._last_rowid = 0

    def available(self) -> bool:
        return (platform.system() == "Darwin" and
                bool(self._handle) and
                shutil.which("osascript") is not None)

    def send(self, text: str, target: str = "") -> None:
        """Send a text iMessage via osascript → Messages.app."""
        recipient = target or self._handle
        if not recipient or not self.available():
            return
        # Escape double-quotes for AppleScript
        safe_text = text.replace("\\", "\\\\").replace('"', '\\"')[:3000]
        script = (
            f'tell application "Messages"\n'
            f'  set targetService to 1st service whose service type = iMessage\n'
            f'  set targetBuddy to buddy "{recipient}" of targetService\n'
            f'  send "{safe_text}" to targetBuddy\n'
            f'end tell'
        )
        try:
            subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=15, check=False)
        except Exception as e:
            log.warning("imessage_send_error", extra={"error": str(e)[:80]})

    def poll(self) -> list[dict]:
        """
        Poll ~/Library/Messages/chat.db for new DM messages.
        Returns messages newer than the last seen ROWID.
        """
        if not self.available() or not self._poll_db:
            return []
        db_path = Path.home() / "Library" / "Messages" / "chat.db"
        if not db_path.exists():
            return []
        try:
            import sqlite3 as _sq3
            con = _sq3.connect(str(db_path), check_same_thread=False)
            try:
                cur = con.execute(
                    """SELECT m.ROWID, h.id, m.text, m.date
                       FROM message m
                       JOIN handle h ON m.handle_id = h.ROWID
                       WHERE m.ROWID > ?
                         AND m.is_from_me = 0
                         AND m.text IS NOT NULL
                       ORDER BY m.ROWID ASC
                       LIMIT 20""",
                    (self._last_rowid,))
                rows = cur.fetchall()
            finally:
                con.close()
        except Exception as e:
            log.debug("imessage_poll_error", extra={"error": str(e)[:80]})
            return []

        msgs: list[dict] = []
        for rowid, sender, text, mac_ts in rows:
            self._last_rowid = max(self._last_rowid, rowid)
            # macOS timestamps are seconds since 2001-01-01; convert to Unix
            unix_ts = mac_ts / 1_000_000_000 + 978_307_200 if mac_ts > 0 else time.time()
            _ci = get_channel_identity(None)
            uid = _ci.resolve("imessage", sender) if _ci else sender
            msgs.append({
                "channel": "imessage",
                "from":    sender,
                "user_id": uid,
                "text":    (text or "").strip(),
                "ts":      unix_ts,
            })
        return msgs


# ══════════════════════════════════════════════════════════════════════════════
