"""A2–A4: secrets and user content must never reach the logs.

Covers invite tokens (A2), WhatsApp webhook signatures (A3) and chat
message text / LLM output (A4).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# Third-party loggers that echo raw request URLs / SQL params in tests but
# are not part of the app's own log output at INFO in production:
# httpx is the TestClient's request line, peewee echoes SQL at DEBUG only.
_TEST_ONLY_LOGGERS = ("httpx", "peewee")


def _app_log_text(caplog) -> str:
    return "\n".join(
        r.getMessage() for r in caplog.records if not r.name.startswith(_TEST_ONLY_LOGGERS)
    )


# ---------------------------------------------------------------- A2 invites


@pytest.fixture
def invite_app(isolated_db):
    from models.admin_users import AdminUserModel
    from routes import admin_admins
    from services.auth.dependencies import get_admin

    app = FastAPI()
    app.include_router(admin_admins.router, prefix="/api/admin")
    state: dict = {"admin": None}

    def _override() -> AdminUserModel:
        return state["admin"]

    app.dependency_overrides[get_admin] = _override

    def set_admin(admin: AdminUserModel) -> None:
        state["admin"] = admin

    return TestClient(app), set_admin


@pytest.fixture
def space_invite(isolated_db):
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from models.pending_admins import PendingAdmins

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"s-{uuid.uuid4().hex[:6]}", name="S")
    inviter = AdminUsers.insert(
        id=f"adm-{uuid.uuid4().hex[:6]}", email="owner@example.com", is_active=True, is_superadmin=True
    )
    invitee = AdminUsers.insert(
        id=f"adm-{uuid.uuid4().hex[:6]}", email="invitee@example.com", is_active=True, is_superadmin=False
    )
    stranger = AdminUsers.insert(
        id=f"adm-{uuid.uuid4().hex[:6]}", email="stranger@example.com", is_active=True, is_superadmin=False
    )
    row = PendingAdmins.create_space_invite(
        email="invitee@example.com", invited_by=inviter.id, target_bot_id=bot.id, role="viewer"
    )
    assert row.token
    return {"row": row, "inviter": inviter, "invitee": invitee, "stranger": stranger}


def test_reject_invite_does_not_log_token(invite_app, space_invite, caplog):
    client, set_admin = invite_app
    set_admin(space_invite["invitee"])
    token = space_invite["row"].token
    with caplog.at_level(logging.DEBUG):
        r = client.post(f"/api/admin/admins/invites/{token}/reject")
    assert r.status_code == 204, r.text
    assert "invite.reject" in _app_log_text(caplog)
    assert space_invite["row"].id in _app_log_text(caplog)
    assert token not in _app_log_text(caplog)


def test_revoke_invite_does_not_log_token(invite_app, space_invite, caplog):
    client, set_admin = invite_app
    set_admin(space_invite["inviter"])
    token = space_invite["row"].token
    with caplog.at_level(logging.DEBUG):
        r = client.delete(f"/api/admin/admins/invites/{token}")
    assert r.status_code == 204, r.text
    assert "invite.revoke" in _app_log_text(caplog)
    assert token not in _app_log_text(caplog)


def test_accept_email_mismatch_does_not_log_token(invite_app, space_invite, caplog):
    client, set_admin = invite_app
    set_admin(space_invite["stranger"])
    token = space_invite["row"].token
    with caplog.at_level(logging.DEBUG):
        r = client.post(f"/api/admin/admins/invites/{token}/accept")
    assert r.status_code == 403, r.text
    assert "email mismatch" in _app_log_text(caplog)
    assert token not in _app_log_text(caplog)


def test_access_log_redacts_invite_token_in_path():
    """uvicorn's access log records the URL path; the invite token in it must
    be redacted without breaking uvicorn's positional-args formatter."""
    from uvicorn.logging import AccessFormatter

    from services.auth.logging_filter import RedactSecretsFilter

    token = "qSHuX3TQGkHZZ36R7jGw_ZYRPVFOU54Dl6Vwa1h72iw"
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:5555", "POST", f"/api/admin/admins/invites/{token}/accept", "1.1", 200), None,
    )
    assert RedactSecretsFilter(preserve_args=True).filter(record)
    line = AccessFormatter(fmt="%(client_addr)s %(request_line)s %(status_code)s", use_colors=False).format(record)
    assert token not in line
    assert "/invites/<redacted>/accept" in line


# ------------------------------------------------------- A3 WhatsApp signature


class _FakeRequest:
    def __init__(self, headers: dict, body: bytes):
        self.headers = headers
        self._body = body


@pytest.mark.parametrize("valid", [True, False])
def test_whatsapp_signature_not_logged(monkeypatch, caplog, valid):
    import asyncio

    from channels.whatsapp_cloud import WhatsAppCloudAdapter

    monkeypatch.setenv("META_APP_SECRET", "app-secret")
    body = b'{"entry": []}'
    digest = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    sig = f"sha256={digest}" if valid else f"bogus-{digest}"

    adapter = WhatsAppCloudAdapter.__new__(WhatsAppCloudAdapter)
    with caplog.at_level(logging.DEBUG):
        ok = asyncio.run(adapter.verify_signature(_FakeRequest({"x-hub-signature-256": sig}, body), None))
    assert ok is valid
    assert digest not in caplog.text


# ------------------------------------------------------------- A4 message text


SECRET_MESSAGE = "my salary is 123456 and my SSN is 078-05-1120"


def test_agent_service_does_not_log_message_text(monkeypatch, caplog):
    from services import agent_service as svc_mod

    class _Handler:
        topics = ["t"]

        def process_user_input(self, message, stream, page_id, channel_id=None):
            return ("LLM said something private", [])

    class _SessionManager:
        def get_handler(self, bot_id, user_id, session_id):
            return _Handler()

    service = svc_mod.AgentService()
    service.session_manager = _SessionManager()
    with caplog.at_level(logging.DEBUG):
        result = service.process_message(
            bot_id="bot-x", user_id="user-x", session_id="sess-x", page_id="p", message=SECRET_MESSAGE
        )
    assert result[0] == "LLM said something private"
    assert "Processed message" in caplog.text
    assert f"len={len(SECRET_MESSAGE)}" in caplog.text
    assert SECRET_MESSAGE[:20] not in caplog.text
    assert "LLM said" not in caplog.text


def test_enrichment_does_not_log_llm_output(monkeypatch, caplog):
    from services import enrichment

    class _LLM:
        def invoke(self, *_a, **_kw):
            class _R:
                content = "not json: " + SECRET_MESSAGE

            return _R()

    monkeypatch.setattr(enrichment, "_build_llm_for_bot", lambda _bot_id: _LLM())
    job = enrichment._EnrichmentJob(bot_id="b", session_id="s", message_id=1, user_text=SECRET_MESSAGE)
    with caplog.at_level(logging.DEBUG):
        assert enrichment._classify_one(job) is None
    assert "non-JSON output" in caplog.text
    assert SECRET_MESSAGE[:20] not in caplog.text
