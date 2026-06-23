"""
PIIRedactor — scans and redacts PII from text before sending to LLMs
and before storing in episodic memory.

Wired into APDERouter.complete() (both input and output) and
EpisodicStore.add() to prevent PII leakage and PII storage.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("essence.security.pii_redactor")


class PIIRedactor:
    """
    Regex-based PII scanner and redactor.

    Replaces detected PII with typed placeholders:
      [EMAIL], [PHONE], [SSN], [CREDIT_CARD], [IP_ADDRESS]

    Applied to all LLM inputs AND outputs via APDERouter.complete().
    Also applied to EpisodicStore.add() payloads.
    """

    PATTERNS: dict[str, str] = {
        "email":       r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        "phone":       r"\b(?:\+?\d[\s\-.]?){7,14}\d\b",
        "ssn":         r"\b\d{3}-\d{2}-\d{4}\b",
        "credit_card": r"\b(?:\d[ \-]*?){13,19}\b",
        "ip_address":  r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    }

    _PLACEHOLDER: dict[str, str] = {
        "email":       "[EMAIL]",
        "phone":       "[PHONE]",
        "ssn":         "[SSN]",
        "credit_card": "[CREDIT_CARD]",
        "ip_address":  "[IP_ADDRESS]",
    }

    def __init__(self) -> None:
        self._compiled: dict[str, re.Pattern] = {
            name: re.compile(pattern, re.IGNORECASE)
            for name, pattern in self.PATTERNS.items()
        }

    def scan(self, text: str) -> list[str]:
        """Return a list of PII type names detected in the text."""
        detected: list[str] = []
        for name, pattern in self._compiled.items():
            if pattern.search(text):
                detected.append(name)
        return detected

    @staticmethod
    def _is_credit_card(s: str) -> bool:
        """Luhn-algorithm validation to eliminate false positives."""
        digits = [int(c) for c in s if c.isdigit()]
        if not (13 <= len(digits) <= 19):
            return False
        checksum = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            checksum += d
        return checksum % 10 == 0

    def redact(self, text: str) -> str:
        """Replace all detected PII with typed placeholders.
        Credit card candidates are validated with the Luhn algorithm
        before redaction to eliminate false positives.
        """
        for name, pattern in self._compiled.items():
            placeholder = self._PLACEHOLDER[name]
            if name == "credit_card":
                def _replace_cc(m: re.Match) -> str:
                    raw = m.group(0)
                    if self._is_credit_card(raw):
                        log.debug("pii_redacted", extra={"type": "credit_card"})
                        return placeholder
                    return raw
                text = pattern.sub(_replace_cc, text)
            else:
                new_text = pattern.sub(placeholder, text)
                if new_text != text:
                    log.debug("pii_redacted", extra={"type": name})
                    text = new_text
        return text

    def redact_dict(self, data: dict) -> dict:
        """Recursively redact all string values in a dict."""
        out: dict = {}
        for k, v in data.items():
            if isinstance(v, str):
                out[k] = self.redact(v)
            elif isinstance(v, dict):
                out[k] = self.redact_dict(v)
            elif isinstance(v, list):
                out[k] = [
                    self.redact(item) if isinstance(item, str) else item
                    for item in v
                ]
            else:
                out[k] = v
        return out


# Module-level singleton
_redactor: Optional[PIIRedactor] = None


def get_pii_redactor() -> PIIRedactor:
    global _redactor
    if _redactor is None:
        _redactor = PIIRedactor()
    return _redactor
