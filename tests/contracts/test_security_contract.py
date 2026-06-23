"""Phase 10.4 — Contract tests for security components."""
from __future__ import annotations
import os
import pytest
from essence.security.pii_redactor import PIIRedactor
from essence.security.audit_logger import AuditLogger


class TestPIIRedactor:
    def test_email_redacted(self) -> None:
        r = PIIRedactor()
        result = r.redact("Send to alice@example.com please")
        assert "[EMAIL]" in result
        assert "alice@example.com" not in result

    def test_ssn_redacted(self) -> None:
        r = PIIRedactor()
        result = r.redact("SSN is 123-45-6789")
        assert "[SSN]" in result

    def test_luhn_valid_card_redacted(self) -> None:
        r = PIIRedactor()
        visa = "4532015112830366"
        result = r.redact(f"Card: {visa}")
        assert "[CREDIT_CARD]" in result

    def test_luhn_invalid_not_redacted(self) -> None:
        r = PIIRedactor()
        # 1234567890123456 fails Luhn check
        result = r.redact("ref: 1234567890123456")
        assert "[CREDIT_CARD]" not in result

    def test_no_false_redaction(self) -> None:
        r = PIIRedactor()
        clean = "The weather is nice today."
        assert r.redact(clean) == clean

    def test_luhn_algorithm_direct(self) -> None:
        r = PIIRedactor()
        assert r._is_credit_card("4532015112830366") is True
        assert r._is_credit_card("1234567890123456") is False


class TestAuditLogger:
    def test_log_returns_hash(self) -> None:
        al = AuditLogger()
        h = al.log("test", "agent", "action", "resource", "ok", {})
        assert isinstance(h, str) and len(h) == 64

    def test_chain_grows(self) -> None:
        al = AuditLogger()
        h1 = al.log("e", "a", "x", "r", "ok", {})
        h2 = al.log("e", "a", "y", "r", "ok", {})
        assert h1 != h2

    def test_verify_chain_clean(self) -> None:
        al = AuditLogger()
        al.log("e", "a", "x", "r", "ok", {})
        al.log("e", "a", "y", "r", "ok", {})
        assert al.verify_chain() is True

    def test_hmac_keyed(self, tmp_path) -> None:
        os.environ["Essence_AUDIT_HMAC_KEY"] = "test_secret_key_12345"
        try:
            al = AuditLogger(tmp_path / "audit.db")
            h = al.log("e", "a", "x", "r", "ok", {})
            assert isinstance(h, str) and len(h) == 64
        finally:
            os.environ.pop("Essence_AUDIT_HMAC_KEY", None)
