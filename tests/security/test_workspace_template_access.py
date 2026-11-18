"""S14: only people who can create bots may read or add workspace templates.

A template holds a whole bot config (prompts, topics, forms, alert
recipients, allowed origins), often snapshotted from a real bot. Any
signed-in admin, including a viewer of an unrelated bot or an invitee
with no memberships, could read every template and plant new ones, with
no limit on count or size.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.security.test_config_gate_templates import _drop_created_rows, _template  # noqa: F401


@pytest.fixture
def make_client(isolated_db, monkeypatch):
    from models.admin_users import AdminUsers
    from routes import workspace
    from services.auth.dependencies import get_admin

    monkeypatch.delenv("BOT_CREATION", raising=False)  # default: writers

    def _make(*, superadmin=False, role=None):
        from models.admin_bot_memberships import AdminBotMemberships
        from models.bots import Bots

        admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@x.example",
                                  is_active=True, is_superadmin=superadmin)
        if role:
            bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"t-{uuid.uuid4().hex[:6]}", name="T")
            AdminBotMemberships.grant(f"mbr-{uuid.uuid4()}", admin.id, bot.id, role)
        app = FastAPI()
        app.include_router(workspace.router, prefix="/api/admin")
        app.dependency_overrides[get_admin] = lambda: admin
        return TestClient(app)

    return _make


@pytest.mark.parametrize("role", [None, "viewer"])
def test_non_creators_cannot_read_or_add(make_client, role):
    tid = _template({})
    client = make_client(role=role)
    assert client.get(f"/api/admin/workspace/templates/{tid}").status_code == 403
    r = client.post("/api/admin/workspace/templates", json={"name": "Official HR", "config_json": {}})
    assert r.status_code == 403


@pytest.mark.parametrize("kw", [{"role": "admin"}, {"superadmin": True}])
def test_creators_can_read_and_add(make_client, kw):
    tid = _template({})
    client = make_client(**kw)
    assert client.get(f"/api/admin/workspace/templates/{tid}").status_code == 200
    r = client.post("/api/admin/workspace/templates", json={"name": "Mine", "config_json": {}})
    assert r.status_code == 201, r.text


def test_size_and_count_are_capped(make_client, monkeypatch):
    from routes import workspace

    client = make_client(superadmin=True)
    big = {"system_prompt": "x" * (workspace.MAX_TEMPLATE_CONFIG_BYTES + 1)}
    assert client.post("/api/admin/workspace/templates",
                       json={"name": "Big", "config_json": big}).status_code == 413

    monkeypatch.setattr(workspace, "MAX_WORKSPACE_TEMPLATES", 0)
    r = client.post("/api/admin/workspace/templates", json={"name": "One more", "config_json": {}})
    assert r.status_code == 409
