"""G13f: personal data stays out of logs and the audit trail.

- Admin and invitee email addresses were logged at INFO in the invite
  routes and the sign-in resolver. Those lines now use ids, and the global
  log redaction masks any email address that still reaches a log line.
- A visitor's free-text rating comment (no length limit) was copied into
  the audit log, which every bot viewer can read.
- ``last_login_at`` was written on every authenticated request.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from services.auth.base import Principal


# ------------------------------------------------------------------ masking
@pytest.fixture
def masked_capture():
    from services.auth.logging_filter import install_global

    original = logging.getLogRecordFactory()
    install_global()
    captured = []

    class _H(logging.Handler):
        def emit(self, record):
            captured.append(self.format(record))

    h = _H()
    logger = logging.getLogger("services.anything")
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    yield logger, captured
    logger.removeHandler(h)
    logging.setLogRecordFactory(original)


def test_email_addresses_are_masked_in_every_log_line(masked_capture):
    from services.auth.logging_filter import email_tag

    logger, captured = masked_capture
    logger.info("sent to %s and cc %s", "Jane.Doe@Corp.Example", "x@y.co")
    (line,) = captured
    assert "Jane.Doe" not in line and "x@y.co" not in line
    assert email_tag("jane.doe@corp.example") in line  # same tag for the same address, any case
    assert "at corp.example>" in line


def test_masking_is_stable_and_idempotent():
    from services.auth.logging_filter import _redact

    once = _redact("a: bob@corp.example")
    assert once == _redact(once) and once == _redact("a: BOB@corp.example")
    assert _redact("pinned foo@1.2.3") == "pinned foo@1.2.3"  # not an address


# ------------------------------------------------------------------ call sites
@pytest.fixture
def unmasked(caplog):
    """Bypass the global mask so the tests see what the code itself logs."""
    original = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    caplog.set_level(logging.INFO)
    yield caplog
    logging.setLogRecordFactory(original)


def _principal(email="nobody@corp.example", subject="sub-x"):
    return Principal(issuer="https://idp.test/", subject=subject, email=email, email_verified=True,
                     name="N", raw_claims={})


def test_refused_login_logs_no_email(isolated_db, unmasked):
    from services.auth.user_resolver import AdminNotProvisioned, resolve_or_create_admin

    with pytest.raises(AdminNotProvisioned):
        resolve_or_create_admin(_principal())
    assert "Refusing login" in unmasked.text and "nobody@corp.example" not in unmasked.text


def test_invite_routes_log_no_email(isolated_db, unmasked, monkeypatch):
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from routes import admin_admins
    from services.auth.dependencies import get_admin

    monkeypatch.setattr(admin_admins, "_try_send_invite_email", lambda **kw: None)
    owner = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="owner@corp.example",
                              is_active=True, is_superadmin=True)
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"g-{uuid.uuid4().hex[:6]}", name="G")
    app = FastAPI()
    app.include_router(admin_admins.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: owner
    client = TestClient(app)

    r = client.post("/api/admin/admins/invites", json={"email": "invitee@corp.example",
                                                       "bot_slug": bot.slug, "role": "viewer"})
    assert r.status_code in (200, 201), r.text
    r = client.post("/api/admin/admins/pending", json={"email": "second@corp.example",
                                                        "bot_assignments": []})
    assert r.status_code in (200, 201), r.text
    assert client.delete("/api/admin/admins/pending/second@corp.example").status_code == 204

    # App log lines only: the test client logs the request URL, which has
    # the address in it (the revoke route takes it in the path). In
    # production that is uvicorn's access log, which the global mask covers.
    app_lines = "\n".join(r.getMessage() for r in unmasked.records if not r.name.startswith("httpx"))
    assert "invite.space.created" in app_lines and "admin.pending.revoked" in app_lines
    for address in ("invitee@corp.example", "second@corp.example", "owner@corp.example"):
        assert address not in app_lines


# ------------------------------------------------------------------ rating comment
def test_rating_comment_is_not_audited(isolated_db):
    from models.admin_audit_log import AdminAuditLogs
    from models.bots import Bots
    from models.emly_messages import EMLYMessages
    from routes import widget
    from services.auth.widget_hmac import issue

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"r-{uuid.uuid4().hex[:6]}", name="R")
    msg = EMLYMessages.insert_new_message(bot_id=bot.id, user_id="alice", session_id="s1", role="assistant",
                                          message="a", not_useful=False, expanded_query=None, page="p")
    app = FastAPI()
    app.include_router(widget.router)
    token = issue(bot_id=bot.id, user_id="alice", session_id="s1", key_version=bot.widget_key_version)
    r = TestClient(app).post(f"/widget/{bot.id}/messages/{msg.id}/rate",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"rating": -1, "session_id": "s1", "user_id": "alice",
                                   "free_text": "my account number is 12345678"})
    assert r.status_code == 200, r.text
    (row,) = AdminAuditLogs.list_filtered(bot_id=bot.id, action="widget.message.rated", limit=5)
    assert "free_text" not in row.payload and "12345678" not in str(row.payload)


# ------------------------------------------------------------------ last_login_at
class _Provider:
    def __init__(self, principal):
        self.principal = principal

    def verify_token(self, token, *, allow_expired=False):
        return self.principal


def test_api_requests_dont_write_last_login(isolated_db, monkeypatch):
    from models.admin_users import AdminUser, AdminUsers

    monkeypatch.setenv("AUTH_ALLOW_BEARER", "true")
    from services.auth.dependencies import get_admin, get_auth_provider

    AdminUsers.insert(id="adm-ll", email="ll@corp.example", issuer="https://idp.test/", subject="sub-ll",
                      email_verified=True, name="Old")
    app = FastAPI()

    @app.get("/me")
    def me(admin=Depends(get_admin)):
        return {"id": admin.id}

    app.dependency_overrides[get_auth_provider] = lambda: _Provider(
        _principal(email="LL@corp.example", subject="sub-ll"))
    client = TestClient(app)
    writes = []
    real_update = AdminUser.update
    AdminUser.update = classmethod(lambda cls, *a, **k: writes.append(k) or real_update(*a, **k))
    try:
        for _ in range(3):
            assert client.get("/me", headers={"Authorization": "Bearer t"}).json() == {"id": "adm-ll"}
    finally:
        AdminUser.update = real_update

    assert AdminUsers.get_by_id("adm-ll").last_login_at is None
    # The name changed at the IdP ("Old" -> "N"): synced once, then no more writes.
    assert len(writes) == 1 and "last_login_at" not in writes[0]
    assert AdminUsers.get_by_id("adm-ll").name == "N"


def test_sign_in_still_records_last_login(isolated_db):
    from models.admin_users import AdminUsers
    from services.auth.user_resolver import resolve_or_create_admin

    AdminUsers.insert(id="adm-si", email="si@corp.example", issuer="https://idp.test/", subject="sub-si",
                      email_verified=True)
    before = datetime.utcnow()
    resolve_or_create_admin(_principal(email="si@corp.example", subject="sub-si"))
    stamped = AdminUsers.get_by_id("adm-si").last_login_at
    assert stamped is not None and stamped.replace(tzinfo=None) >= before.replace(microsecond=0)
