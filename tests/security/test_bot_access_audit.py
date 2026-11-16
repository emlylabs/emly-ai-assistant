"""G4a: superadmin access to a bot they aren't a member of is audited.
G4b: a bot's audit log and channel-secret hints are for owners and admins.

Superadmins skip every per-bot check, and that was only debug-logged, so
nothing recorded a superadmin reading another department's conversations.
Viewers could read the bot's audit log (who did what, filterable by
admin) and the last 4 characters of channel secrets.
"""
from __future__ import annotations

import json
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
    from routes import admin_audit, admin_bot_channels, admin_bot_sessions
    from services import authz
    from services.audit import AuditRequestContextMiddleware
    from services.auth.dependencies import get_admin

    getattr(authz, "_bypass_seen", {}).clear()
    bot = Bots.insert(id=_uid("bot"), slug=_uid("acc"), name="HR bot")
    current = {}
    app = FastAPI()
    for r in (admin_audit, admin_bot_channels, admin_bot_sessions):
        app.include_router(r.router, prefix="/api/admin")
    app.add_middleware(AuditRequestContextMiddleware)
    app.dependency_overrides[get_admin] = lambda: current["admin"]

    def login(role=None, *, superadmin=False):
        admin = AdminUsers.insert(id=_uid("adm"), email=f"{_uid('a')}@example.com", is_active=True,
                                  is_superadmin=superadmin)
        if role:
            AdminBotMemberships.grant(id=_uid("mbr"), admin_id=admin.id, bot_id=bot.id, role=role)
        current["admin"] = admin
        return admin

    yield TestClient(app), bot, login
    getattr(authz, "_bypass_seen", {}).clear()


def _bypass_rows(admin_id):
    from models.admin_audit_log import AdminAuditLogs

    return AdminAuditLogs.list_filtered(admin_id=admin_id, action="authz.superadmin_bypass", limit=50)


# -------------------------------------------------------------------- G4a
def test_non_member_superadmin_read_is_audited_once_per_window(api):
    client, bot, login = api
    admin = login(superadmin=True)

    for _ in range(3):
        assert client.get(f"/api/admin/bots/{bot.slug}/sessions").status_code == 200

    (row,) = _bypass_rows(admin.id)
    assert row.bot_id == bot.id
    assert row.payload == {"needed": "viewer", "method": "GET", "route": "/api/admin/bots/{slug}/sessions"}


def test_each_route_is_recorded(api):
    client, bot, login = api
    admin = login(superadmin=True)
    client.get(f"/api/admin/bots/{bot.slug}/sessions")
    client.get(f"/api/admin/bots/{bot.slug}/sessions/daily")
    client.get(f"/api/admin/bots/{bot.slug}/channels")  # inline bypass used to skip authz entirely
    routes = sorted(r.payload["route"] for r in _bypass_rows(admin.id))
    assert routes == ["/api/admin/bots/{slug}/channels", "/api/admin/bots/{slug}/sessions",
                      "/api/admin/bots/{slug}/sessions/daily"]


def test_window_expiry_records_again(api, monkeypatch):
    from services import authz

    client, bot, login = api
    admin = login(superadmin=True)
    client.get(f"/api/admin/bots/{bot.slug}/sessions")
    monkeypatch.setattr(authz, "SUPERADMIN_AUDIT_WINDOW_SECONDS", 0)
    client.get(f"/api/admin/bots/{bot.slug}/sessions")
    assert len(_bypass_rows(admin.id)) == 2


def test_superadmin_with_enough_membership_is_not_recorded(api):
    client, bot, login = api
    admin = login("owner", superadmin=True)
    client.get(f"/api/admin/bots/{bot.slug}/sessions")
    client.get(f"/api/admin/bots/{bot.slug}/audit-logs")
    assert _bypass_rows(admin.id) == []


def test_superadmin_viewer_using_writer_access_is_recorded(api):
    client, bot, login = api
    admin = login("viewer", superadmin=True)
    client.get(f"/api/admin/bots/{bot.slug}/sessions")  # covered by their viewer role
    assert client.get(f"/api/admin/bots/{bot.slug}/audit-logs").status_code == 200
    (row,) = _bypass_rows(admin.id)
    assert row.payload["needed"] == "admin"


def test_regular_member_is_not_recorded(api):
    client, bot, login = api
    admin = login("viewer")
    client.get(f"/api/admin/bots/{bot.slug}/sessions")
    assert _bypass_rows(admin.id) == []


# -------------------------------------------------------------------- G4b
@pytest.mark.parametrize("role, status", [("viewer", 403), ("admin", 200), ("owner", 200)])
def test_bot_audit_log_is_for_writers(api, role, status):
    client, bot, login = api
    login(role)
    assert client.get(f"/api/admin/bots/{bot.slug}/audit-logs").status_code == status
    assert client.get(f"/api/admin/audit-logs?bot_id={bot.id}").status_code == status


@pytest.mark.parametrize("role, hint", [("viewer", "…"), ("admin", "…cdef")])
def test_channel_secret_hints_are_for_writers(api, role, hint):
    import channels.telegram  # noqa: F401  (registers the adapter, as main does)
    from models.bot_channels import BotChannels
    from services import secrets

    client, bot, login = api
    BotChannels.insert(id=_uid("ch"), bot_id=bot.id, type="telegram",
                       credentials_encrypted=secrets.encrypt(json.dumps({"bot_token": "123:abcdef"})))
    login(role)
    (channel,) = client.get(f"/api/admin/bots/{bot.slug}/channels").json()
    assert channel["secrets_redacted"]["bot_token"] == hint
