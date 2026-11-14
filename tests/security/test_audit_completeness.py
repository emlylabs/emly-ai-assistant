"""G5b: routes that refuse access themselves still write ``authz.denied``.
G5c: a failed audit write is loud, and never logs the payload.

Several routes raised their own 403 instead of going through
``services.authz``, so those denials never reached the audit log. Audit
write failures were logged with a traceback (which can echo payload
values through SQL parameters) and were otherwise invisible.
"""
from __future__ import annotations

import logging
import uuid

import pytest


def _uid(p):
    return f"{p}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def api(isolated_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from routes import admin_admins, admin_audit, admin_bot_channels
    from services.auth.dependencies import get_admin

    bot = Bots.insert(id=_uid("bot"), slug=_uid("den"), name="Den")
    current = {}
    app = FastAPI()
    for r in (admin_admins, admin_audit, admin_bot_channels):
        app.include_router(r.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: current["admin"]

    def login(role=None):
        admin = AdminUsers.insert(id=_uid("adm"), email=f"{_uid('a')}@example.com", is_active=True)
        if role:
            AdminBotMemberships.grant(id=_uid("mbr"), admin_id=admin.id, bot_id=bot.id, role=role)
        current["admin"] = admin
        return admin

    return TestClient(app), bot, login


def _denials(admin_id):
    from models.admin_audit_log import AdminAuditLogs

    return AdminAuditLogs.list_filtered(admin_id=admin_id, action="authz.denied", limit=10)


def test_channel_route_denial_is_audited_and_body_unchanged(api):
    client, bot, login = api
    admin = login()
    r = client.get(f"/api/admin/bots/{bot.slug}/channels")
    assert (r.status_code, r.json()["detail"]) == (403, "not a member of this bot")
    (row,) = _denials(admin.id)
    assert row.bot_id == bot.id and row.payload == {"code": "insufficient_role", "needed": "viewer"}


def test_workspace_audit_feed_denial_is_audited(api):
    client, _, login = api
    admin = login()
    r = client.get("/api/admin/audit-logs")
    assert (r.status_code, r.json()["detail"]) == (403, "bot_id required for non-superadmin reads")
    assert [d.payload["code"] for d in _denials(admin.id)] == ["not_superadmin"]


def test_ownership_transfer_denial_is_audited(api):
    client, bot, login = api
    admin = login("admin")
    r = client.post(f"/api/admin/bots/{bot.slug}/transfer-ownership",
                    json={"bot_slug": bot.slug, "new_owner_admin_id": "x"})
    assert (r.status_code, r.json()["detail"]) == (403, "Only owners can transfer ownership")
    assert [d.payload["code"] for d in _denials(admin.id)] == ["not_owner"]


def test_channel_install_denial_is_audited(isolated_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import channels.whatsapp_cloud  # noqa: F401  (registers an OAuth-install adapter, as main does)
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from routes import channels as channels_route
    from services.auth.dependencies import get_admin

    bot = Bots.insert(id=_uid("bot"), slug=_uid("ins"), name="I")
    admin = AdminUsers.insert(id=_uid("adm"), email=f"{_uid('a')}@example.com", is_active=True)
    app = FastAPI()
    app.include_router(channels_route.router)
    app.dependency_overrides[get_admin] = lambda: admin
    r = TestClient(app).get(f"/channels/whatsapp_cloud/install?bot_id={bot.id}", follow_redirects=False)
    assert r.status_code == 403
    (row,) = _denials(admin.id)
    assert row.payload == {"code": "not_writer", "channel_type": "whatsapp_cloud"}


# ------------------------------------------------------------------ G5c
def test_failed_write_is_loud_counted_and_hides_the_payload(isolated_db, monkeypatch, caplog):
    from models.admin_audit_log import AdminAuditLogs
    from services import audit as audit_mod

    def boom(**kw):
        raise RuntimeError(f"insert failed with params {kw['payload']}")

    monkeypatch.setattr(AdminAuditLogs, "insert", boom)
    before = audit_mod.audit_write_failures()

    with caplog.at_level(logging.ERROR, logger="services.audit"):
        audit_mod.audit("admin.updated", admin_id="a1", payload={"email": "secret@example.com"})

    assert audit_mod.audit_write_failures() == before + 1
    (rec,) = [r for r in caplog.records if "AUDIT WRITE FAILED" in r.getMessage()]
    assert rec.levelno == logging.ERROR
    assert "admin.updated" in rec.getMessage() and "RuntimeError" in rec.getMessage()
    assert "secret@example.com" not in caplog.text


def test_failures_show_on_readyz(monkeypatch):
    import main
    from services import audit as audit_mod

    monkeypatch.setattr(audit_mod, "_WRITE_FAILURES", 3)
    codes = {w["code"]: w["message"] for w in main._collect_runtime_warnings()}
    assert codes["audit_write_failures"].startswith("3 audit log write(s) failed")


# ------------------------------------------------------------------ Q-G5a
@pytest.fixture
def broken_audit(isolated_db, monkeypatch):
    """An app whose audit table can't be written, with main's 503 handler."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from models.admin_audit_log import AdminAuditLogs
    from services.audit import AuditRequestContextMiddleware, AuditUnavailable, audit

    def boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(AdminAuditLogs, "insert", boom)
    did = []
    app = FastAPI()

    @app.post("/do/{action}")
    def do(action: str):
        audit(action, admin_id="a1")
        did.append(action)
        return {"ok": True}

    import main

    app.add_exception_handler(AuditUnavailable, main.app.exception_handlers[AuditUnavailable])
    app.add_middleware(AuditRequestContextMiddleware)
    return TestClient(app), did, monkeypatch


@pytest.mark.parametrize("action", ["auth.login", "admin.updated", "membership.granted", "bot.created",
                                    "config.updated", "channel.secrets_rotated"])
def test_prod_security_event_fails_the_request(broken_audit, action):
    client, did, mp = broken_audit
    mp.setenv("MODE", "prod")
    mp.delenv("AUDIT_FAIL_CLOSED", raising=False)
    r = client.post(f"/do/{action}")
    assert (r.status_code, r.json()) == (503, {"detail": {"code": "audit_unavailable"}})
    assert did == []


@pytest.mark.parametrize("mode, flag, action", [
    ("prod", None, "message.updated"),  # content event: logged only
    ("dev", None, "auth.login"),        # dev default is off
    ("prod", "false", "auth.login"),    # explicit opt-out
])
def test_other_cases_carry_on(broken_audit, mode, flag, action):
    client, did, mp = broken_audit
    mp.setenv("MODE", mode)
    if flag is None:
        mp.delenv("AUDIT_FAIL_CLOSED", raising=False)
    else:
        mp.setenv("AUDIT_FAIL_CLOSED", flag)
    assert client.post(f"/do/{action}").status_code == 200
    assert did == [action]


def test_background_jobs_only_log(isolated_db, monkeypatch):
    from models.admin_audit_log import AdminAuditLogs
    from services.audit import audit

    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setattr(AdminAuditLogs, "insert", lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    audit("bot.purged", admin_id=None)  # no request in flight: must not raise


def test_unaudited_superadmin_bypass_is_refused(isolated_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import main
    from models.admin_audit_log import AdminAuditLogs
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from routes import admin_bot_sessions
    from services import authz
    from services.audit import AuditRequestContextMiddleware, AuditUnavailable
    from services.auth.dependencies import get_admin

    authz._bypass_seen.clear()
    monkeypatch.setenv("MODE", "prod")
    bot = Bots.insert(id=_uid("bot"), slug=_uid("fc"), name="FC")
    admin = AdminUsers.insert(id=_uid("adm"), email=f"{_uid('s')}@example.com", is_active=True,
                              is_superadmin=True)
    app = FastAPI()
    app.include_router(admin_bot_sessions.router, prefix="/api/admin")
    app.add_middleware(AuditRequestContextMiddleware)
    app.add_exception_handler(AuditUnavailable, main.app.exception_handlers[AuditUnavailable])
    app.dependency_overrides[get_admin] = lambda: admin
    monkeypatch.setattr(AdminAuditLogs, "insert", lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))

    client = TestClient(app)
    assert client.get(f"/api/admin/bots/{bot.slug}/sessions").status_code == 503
    # The failed write didn't open the 10-minute window.
    assert client.get(f"/api/admin/bots/{bot.slug}/sessions").status_code == 503
    authz._bypass_seen.clear()
