"""C7 (part 3): CSV export (superadmin / bot owner) and retention purge."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def env(isolated_db):
    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from routes import admin_audit
    from services.auth.dependencies import get_admin

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"e-{uuid.uuid4().hex[:6]}", name="E")
    other = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"o-{uuid.uuid4().hex[:6]}", name="O")

    def admin(role=None, superadmin=False):
        a = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@x.example",
                              is_active=True, is_superadmin=superadmin)
        if role:
            AdminBotMemberships.grant(id=f"mbr-{uuid.uuid4()}", admin_id=a.id, bot_id=bot.id, role=role)
        return a

    app = FastAPI()
    app.include_router(admin_audit.router, prefix="/api/admin")
    state: dict = {}
    app.dependency_overrides[get_admin] = lambda: state["admin"]

    def as_(a):
        state["admin"] = a
        return TestClient(app)

    return {"bot": bot, "other": other, "admin": admin, "as": as_}


def _parse(resp):
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    return list(csv.DictReader(io.StringIO(resp.text)))


def test_superadmin_exports_everything(env):
    from services.audit import audit

    root = env["admin"](superadmin=True)
    audit("bot.created", admin_id=root.id, bot_id=env["bot"].id, ip="10.0.0.1", ua="ua-1")
    audit("bot.created", admin_id=root.id, bot_id=env["other"].id)
    rows = _parse(env["as"](root).get("/api/admin/audit/export"))
    created = [r for r in rows if r["action"] == "bot.created"]
    assert len(created) == 2
    assert created[0]["admin_email"] == root.email
    assert created[0]["ip"] == "10.0.0.1" and created[0]["user_agent"] == "ua-1"


def test_non_superadmin_cannot_export_workspace(env):
    owner = env["admin"]("owner")
    assert env["as"](owner).get("/api/admin/audit/export").status_code == 403


def test_owner_exports_only_their_bot(env):
    from services.audit import audit

    owner = env["admin"]("owner")
    audit("config.saved", bot_id=env["bot"].id)
    audit("config.saved", bot_id=env["other"].id)
    rows = _parse(env["as"](owner).get(f"/api/admin/bots/{env['bot'].slug}/audit/export"))
    assert {r["bot_id"] for r in rows if r["action"] == "config.saved"} == {env["bot"].id}


@pytest.mark.parametrize("role", ["admin", "viewer", None])
def test_non_owners_cannot_export_bot(env, role):
    a = env["admin"](role)
    assert env["as"](a).get(f"/api/admin/bots/{env['bot'].slug}/audit/export").status_code == 403


def test_export_is_itself_audited(env):
    from models.admin_audit_log import AdminAuditLogs

    root = env["admin"](superadmin=True)
    env["as"](root).get("/api/admin/audit/export?action=bot.created")
    (row,) = AdminAuditLogs.list_filtered(action="audit.exported")
    assert row.admin_id == root.id and row.payload == {"action": "bot.created"}


def test_csv_neutralises_formula_injection(env):
    from services.audit import audit

    root = env["admin"](superadmin=True)
    audit("auth.local.login_failed", success=False, ua='=HYPERLINK("http://evil","x")',
          payload={"email": "@evil"})
    rows = _parse(env["as"](root).get("/api/admin/audit/export?action=auth.local.login_failed"))
    assert rows[0]["user_agent"].startswith("'=")


def test_export_time_window(env):
    from models.admin_audit_log import AdminAuditLog
    from services.audit import audit

    root = env["admin"](superadmin=True)
    audit("x.old")
    audit("x.new")
    AdminAuditLog.update(created_at=datetime.now(timezone.utc) - timedelta(days=10)).where(
        AdminAuditLog.action == "x.old").execute()
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    rows = _parse(env["as"](root).get("/api/admin/audit/export", params={"since": since}))
    actions = {r["action"] for r in rows}
    assert "x.new" in actions and "x.old" not in actions


# ---------------------------------------------------------------- retention


def test_retention_purge_removes_only_old_rows(isolated_db, monkeypatch):
    from models.admin_audit_log import AdminAuditLog, AdminAuditLogs
    from services.audit import audit, purge_expired_audit_rows

    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "30")
    audit("keep.me")
    audit("drop.me")
    AdminAuditLog.update(created_at=datetime.now(timezone.utc) - timedelta(days=31)).where(
        AdminAuditLog.action == "drop.me").execute()
    assert purge_expired_audit_rows() == 1
    assert AdminAuditLogs.list_filtered(action="drop.me") == []
    assert len(AdminAuditLogs.list_filtered(action="keep.me")) == 1
    (purge,) = AdminAuditLogs.list_filtered(action="audit.purged")
    assert purge.payload["removed"] == 1 and purge.payload["retention_days"] == 30


def test_retention_zero_keeps_everything(isolated_db, monkeypatch):
    from models.admin_audit_log import AdminAuditLog
    from services.audit import audit, purge_expired_audit_rows

    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "0")
    audit("ancient")
    AdminAuditLog.update(created_at=datetime(2000, 1, 1, tzinfo=timezone.utc)).execute()
    assert purge_expired_audit_rows() == 0


def test_default_retention_is_365(monkeypatch):
    from services.audit import audit_retention_days

    monkeypatch.delenv("AUDIT_RETENTION_DAYS", raising=False)
    assert audit_retention_days() == 365


def test_purge_job_is_resolvable_by_name():
    """The scheduler stores the job by import path; it must resolve."""
    from apscheduler.util import ref_to_obj

    from services.audit import purge_expired_audit_rows

    assert ref_to_obj("services.audit:purge_expired_audit_rows") is purge_expired_audit_rows
