"""A8: STARTTLS verifies the server certificate. A9: no default SMTP relay."""

from __future__ import annotations

import logging
import ssl
from email.mime.text import MIMEText

import pytest


class _FakeSMTP:
    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, context=None):
        self.host, self.port, self.ctor_context = host, port, context
        self.starttls_context = "not-called"
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.starttls_context = context

    def login(self, *_a):
        pass

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeSMTP.instances = []


def _assert_verifying(ctx):
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_starttls_on_587_verifies_certificate(monkeypatch):
    from services import email_service

    monkeypatch.setattr(email_service.smtplib, "SMTP", _FakeSMTP)
    sender = email_service.EmailSender("smtp.example.com", 587, "u", "p")
    assert sender.send_email(MIMEText("hi")) is True
    (smtp,) = _FakeSMTP.instances
    _assert_verifying(smtp.starttls_context)


def test_implicit_tls_on_465_verifies_certificate(monkeypatch):
    from services import email_service

    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", _FakeSMTP)
    sender = email_service.EmailSender("smtp.example.com", 465, "u", "p")
    assert sender.send_email(MIMEText("hi")) is True
    (smtp,) = _FakeSMTP.instances
    _assert_verifying(smtp.ctor_context)


def test_no_default_email_server(monkeypatch):
    import config

    monkeypatch.delenv("EMAIL_SERVER", raising=False)
    # Re-evaluate just the env lookup rather than reloading config (which
    # would reload the embedding model).
    src = open(config.__file__).read()
    assert "smtp.resend.com" not in src
    line = next(l for l in src.splitlines() if l.startswith("EMAIL_SERVER ="))
    import os

    assert eval(line.split("=", 1)[1], {"os": os}) is None


def test_send_is_skipped_without_server(monkeypatch, caplog):
    from services import email_service

    def _boom(*_a, **_kw):
        raise AssertionError("must not connect when EMAIL_SERVER is unset")

    monkeypatch.setattr(email_service.smtplib, "SMTP", _boom)
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", _boom)
    with caplog.at_level(logging.WARNING):
        assert email_service.EmailSender(None, 465, "", "").send_email(MIMEText("hi")) is False
    assert "EMAIL_SERVER not set" in caplog.text


def test_boot_status_line(monkeypatch, caplog):
    from services import email_service

    monkeypatch.setattr(email_service, "EMAIL_SERVER", None)
    with caplog.at_level(logging.INFO):
        email_service.log_email_status()
    assert "Email disabled" in caplog.text
    assert email_service.email_enabled() is False

    monkeypatch.setattr(email_service, "EMAIL_SERVER", "smtp.example.com")
    assert email_service.email_enabled() is True


def test_otp_email_job_does_not_crash_without_server(monkeypatch):
    """The OTP email job runs in the scheduler; with email disabled it
    must return quietly instead of raising."""
    from services import otp

    monkeypatch.setattr(otp, "EMAIL_SERVER", None)
    otp.send_otp_in_email({"user_id": "a@example.com", "otp": "123456"})
