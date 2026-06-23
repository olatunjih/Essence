"""WhatsApp, Gmail, Slack, Signal adapters."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *
from essence.channels.telegram import ChannelAdapter, TelegramAdapter, DiscordAdapter  # noqa: F401
from essence.channels.extended2 import MatrixAdapter, TeamsAdapter, LineAdapter, IMessageAdapter  # noqa: F401

# EXTENDED CHANNEL ADAPTERS  (WhatsApp · Gmail · Slack · Signal)
# ══════════════════════════════════════════════════════════════════════════════
# All adapters follow the ChannelAdapter base interface (send / poll / available).
# Credentials are loaded via SecretsVault → os.environ fallback.
# None of these require extra packages at import time; they gate on availability.

class WhatsAppAdapter(ChannelAdapter):
    """
    WhatsApp Business Cloud API adapter.
    Requires: WHATSAPP_TOKEN (permanent access token) + WHATSAPP_PHONE_ID.
    Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
    """
    NAME = "whatsapp"
    _BASE = "https://graph.facebook.com/v19.0"

    def __init__(self, token: str = "", phone_id: str = "") -> None:
        self.token    = token    or os.environ.get("WHATSAPP_TOKEN", "")
        self.phone_id = phone_id or os.environ.get("WHATSAPP_PHONE_ID", "")

    def available(self) -> bool:
        return bool(self.token and self.phone_id)

    def send(self, text: str, target: str) -> None:
        """Send a text message to a phone number (E.164 format, e.g. +1234567890)."""
        if not self.available():
            return
        url  = f"{self._BASE}/{self.phone_id}/messages"
        body = json.dumps({
            "messaging_product": "whatsapp",
            "to":   target,
            "type": "text",
            "text": {"body": text[:4096]},
        }).encode()
        req  = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.token}"},
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=12)
        except Exception as e:
            log.warning("whatsapp_send_error", extra={"error": str(e)[:80]})

    def poll(self) -> list[dict]:
        """
        WhatsApp Business API is webhook-only — messages arrive via POST.
        This poll() is a no-op; wire up /api/webhooks/receive?source=whatsapp
        in the FastAPI server instead.
        """
        return []

    @staticmethod
    def verify_webhook(token: str, challenge: str) -> str | None:
        """Handle the GET verification challenge from Meta."""
        expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
        if expected and token == expected:
            return challenge
        return None


class GmailAdapter(ChannelAdapter):
    """
    Gmail IMAP adapter for polling unread messages.
    Requires: GMAIL_ADDRESS + GMAIL_APP_PASSWORD (Google App Password).
    For send, uses SMTP via ssl. For receive, polls IMAP INBOX.
    Enable: Google Account → Security → App Passwords → Mail.
    """
    NAME = "gmail"

    def __init__(self, address: str = "", app_password: str = "") -> None:
        self.address      = address      or os.environ.get("GMAIL_ADDRESS", "")
        self.app_password = app_password or os.environ.get("GMAIL_APP_PASSWORD", "")

    def available(self) -> bool:
        return bool(self.address and self.app_password)

    def send(self, text: str, target: str) -> None:
        """Send an email via Gmail SMTP."""
        if not self.available():
            return
        import smtplib, email.mime.text as _emt
        msg           = _emt.MIMEText(text)
        msg["Subject"] = "Essence Message"
        msg["From"]    = self.address
        msg["To"]      = target
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(self.address, self.app_password)
                smtp.send_message(msg)
        except Exception as e:
            log.warning("gmail_send_error", extra={"error": str(e)[:80]})

    def poll(self) -> list[dict]:
        """Fetch unread messages from INBOX via IMAP."""
        if not self.available():
            return []
        import imaplib, email as _email, email.header as _hdr
        msgs: list[dict] = []
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
                imap.login(self.address, self.app_password)
                imap.select("INBOX")
                _, ids = imap.search(None, "UNSEEN")
                for eid in (ids[0].split() if ids[0] else [])[:10]:
                    _, data = imap.fetch(eid, "(RFC822)")
                    for part in data:
                        if isinstance(part, tuple):
                            msg   = _email.message_from_bytes(part[1])
                            subj  = _hdr.decode_header(msg["Subject"] or "")[0]
                            subj  = (subj[0].decode(subj[1] or "utf-8")
                                     if isinstance(subj[0], bytes) else subj[0])
                            body  = ""
                            if msg.is_multipart():
                                for p in msg.walk():
                                    if p.get_content_type() == "text/plain":
                                        body = p.get_payload(decode=True)                                                 .decode("utf-8", errors="replace")
                                        break
                            else:
                                body = msg.get_payload(decode=True)                                            .decode("utf-8", errors="replace")
                            msgs.append({
                                "from": msg.get("From", ""),
                                "subject": subj,
                                "text": f"Subject: {subj}\n\n{body[:500]}",
                                "ts": time.time(),
                            })
        except Exception as e:
            log.debug("gmail_poll_error", extra={"error": str(e)[:80]})
        return msgs


class SlackAdapter(ChannelAdapter):
    """
    Slack Web API adapter.
    Requires: SLACK_BOT_TOKEN (xoxb-...) + optionally SLACK_CHANNEL_ID.
    For incoming messages, configure a Slack Event Subscription pointing
    to /api/webhooks/receive?source=slack on the Essence server.
    """
    NAME = "slack"
    _BASE = "https://slack.com/api"

    def __init__(self, token: str = "", channel: str = "") -> None:
        self.token   = token   or os.environ.get("SLACK_BOT_TOKEN", "")
        self.channel = channel or os.environ.get("SLACK_CHANNEL_ID", "")

    def available(self) -> bool:
        return bool(self.token)

    def send(self, text: str, target: str) -> None:
        """Post a message to a channel or DM."""
        if not self.available():
            return
        ch  = target or self.channel
        if not ch:
            log.warning("slack_send_no_channel")
            return
        body = json.dumps({"channel": ch, "text": text[:3000]}).encode()
        req  = urllib.request.Request(
            f"{self._BASE}/chat.postMessage",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.token}"},
            method="POST")
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
            if not resp.get("ok"):
                log.warning("slack_send_error",
                            extra={"error": resp.get("error", "unknown")})
        except Exception as e:
            log.warning("slack_send_exception", extra={"error": str(e)[:80]})

    def poll(self) -> list[dict]:
        """
        Fetch recent messages from the configured channel via conversations.history.
        Note: For real-time delivery, prefer Slack Event Subscriptions → webhook.
        """
        if not self.available() or not self.channel:
            return []
        params = urllib.parse.urlencode({
            "channel": self.channel, "limit": 10})
        req = urllib.request.Request(
            f"{self._BASE}/conversations.history?{params}",
            headers={"Authorization": f"Bearer {self.token}"})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=8).read())
            msgs = []
            for m in data.get("messages", []):
                if m.get("subtype"):
                    continue   # skip join/leave/bot messages
                msgs.append({
                    "from": m.get("user", ""),
                    "text": m.get("text", ""),
                    "ts":   float(m.get("ts", time.time())),
                })
            return msgs
        except Exception as e:
            log.debug("slack_poll_error", extra={"error": str(e)[:60]})
            return []


class ChannelRegistry:
    """
    Central registry of all configured channel adapters.
    Provides a unified broadcast / poll interface for the agent layer.

    Usage:
        registry = ChannelRegistry.from_env()
        registry.broadcast("Hello from Essence!")        # send to all active
        messages = registry.poll_all()                # gather from all active
    """

    def __init__(self, adapters: "list[ChannelAdapter] | None" = None) -> None:
        self._adapters: list[ChannelAdapter] = adapters or []

    @classmethod
    def from_env(cls) -> "ChannelRegistry":
        """Auto-configure all adapters from environment variables."""
        return cls([
            TelegramAdapter(),
            DiscordAdapter(),
            WhatsAppAdapter(),
            GmailAdapter(),
            SlackAdapter(),
            MatrixAdapter(),
            TeamsAdapter(),
            LineAdapter(),
            IMessageAdapter(),
        ])

    def active(self) -> "list[ChannelAdapter]":
        """Return only adapters that have their credentials configured."""
        return [a for a in self._adapters if a.available()]

    def broadcast(self, text: str, target: str = "") -> int:
        """Send text to all active adapters. Returns number of adapters reached."""
        n = 0
        for a in self.active():
            try:
                a.send(text, target)
                n += 1
            except Exception as e:
                log.warning("channel_broadcast_error",
                            extra={"adapter": a.NAME, "error": str(e)[:60]})
        return n

    def poll_all(self) -> "list[dict]":
        """Poll all active adapters; returns combined message list sorted by ts."""
        all_msgs: list[dict] = []
        for a in self.active():
            try:
                msgs = a.poll()
                for m in msgs:
                    m["_channel"] = a.NAME
                all_msgs.extend(msgs)
            except Exception as e:
                log.debug("channel_poll_error",
                          extra={"adapter": a.NAME, "error": str(e)[:60]})
        all_msgs.sort(key=lambda m: m.get("ts", 0))
        return all_msgs

    def status(self) -> str:
        """One-line status string for CLI display."""
        parts = []
        for a in self._adapters:
            status = "✓" if a.available() else "✗"
            parts.append(f"{a.NAME}:{status}")
        return "  ".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
