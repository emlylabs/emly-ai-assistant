"""Tests for audit-log writes and the secret-redaction logging filter."""

from __future__ import annotations

import logging

import pytest


# -------- audit log --------


def test_audit_writes_a_row(isolated_db):
    from models.admin_audit_log import AdminAuditLogs
    from services.audit import audit

    audit("auth.login", admin_id="adm-1", payload={"issuer": "https://idp.test/"})
    rows = AdminAuditLogs.list_filtered(action="auth.login")
    assert len(rows) == 1
    assert rows[0].admin_id == "adm-1"
    assert rows[0].success is True
    assert rows[0].payload == {"issuer": "https://idp.test/"}


def test_audit_records_failure_with_success_false(isolated_db):
    from models.admin_audit_log import AdminAuditLogs
    from services.audit import audit

    audit("auth.token_invalid", success=False, payload={"reason": "bad_signature"})
    rows = AdminAuditLogs.list_filtered(action="auth.token_invalid", success=False)
    assert len(rows) == 1
    assert rows[0].success is False


def test_audit_filter_by_admin_id(isolated_db):
    from models.admin_audit_log import AdminAuditLogs
    from services.audit import audit

    audit("auth.login", admin_id="adm-A")
    audit("auth.login", admin_id="adm-B")
    audit("auth.logout", admin_id="adm-A")
    a_rows = AdminAuditLogs.list_filtered(admin_id="adm-A")
    assert {r.action for r in a_rows} == {"auth.login", "auth.logout"}


def test_audit_filter_by_bot_id(isolated_db):
    from models.admin_audit_log import AdminAuditLogs
    from services.audit import audit

    audit("bot.created", bot_id="bot-1", admin_id="adm-1")
    audit("bot.deleted", bot_id="bot-2", admin_id="adm-1")
    rows = AdminAuditLogs.list_filtered(bot_id="bot-1")
    assert len(rows) == 1
    assert rows[0].action == "bot.created"


def test_audit_never_raises_on_failure(isolated_db, monkeypatch):
    """A DB blip must not propagate from audit() — auditing is best-effort."""
    from models import admin_audit_log
    from services.audit import audit

    def boom(*a, **kw):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(admin_audit_log.AdminAuditLogs, "insert", boom)
    # Even with the inner write blowing up, the outer call returns normally.
    audit("auth.login", admin_id="adm-x")


# -------- redaction filter --------


@pytest.fixture
def redaction_logger():
    """A throwaway logger with the redaction filter installed and a capture handler."""
    from services.auth.logging_filter import install

    captured: list[str] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            captured.append(self.format(record))

    logger = logging.getLogger(f"test_redaction_{id(captured)}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    handler = CaptureHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    install(logger)
    return logger, captured


def test_authorization_bearer_redacted(redaction_logger):
    logger, captured = redaction_logger
    logger.info("Outbound request: Authorization: Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6ImFiYyJ9.foo.bar")
    assert "Bearer <redacted>" in captured[-1]
    assert "eyJhbGc" not in captured[-1]


def test_cookie_header_redacted(redaction_logger):
    logger, captured = redaction_logger
    logger.info("Cookie: emly_admin_session=eyJfoo")
    assert "<redacted>" in captured[-1]
    assert "eyJfoo" not in captured[-1]


def test_set_cookie_header_redacted(redaction_logger):
    logger, captured = redaction_logger
    logger.info("Set-Cookie: emly_admin_session=very-secret-token; Path=/; HttpOnly")
    assert "<redacted>" in captured[-1]
    assert "very-secret-token" not in captured[-1]


def test_password_field_in_dict_repr_redacted(redaction_logger):
    logger, captured = redaction_logger
    logger.info("Body received: {'email': 'a@b.c', 'password': 'hunter2'}")
    assert "hunter2" not in captured[-1]
    assert "<redacted>" in captured[-1]


def test_access_token_field_redacted(redaction_logger):
    logger, captured = redaction_logger
    logger.info("Token response: {\"access_token\": \"eyJ123\", \"token_type\": \"Bearer\"}")
    assert "eyJ123" not in captured[-1]
    assert "<redacted>" in captured[-1]


def test_non_secret_text_unchanged(redaction_logger):
    logger, captured = redaction_logger
    logger.info("Login attempted for alice@example.com")
    assert captured[-1] == "Login attempted for alice@example.com"
