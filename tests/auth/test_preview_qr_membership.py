"""C3: the share-preview QR is only for members of the bot."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def env(isolated_db):
    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from routes import admin_widget
    from services.auth.dependencies import get_admin

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"q-{uuid.uuid4().hex[:6]}", name="Q")

    def admin(role=None, superadmin=False):
        a = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@x.example",
                              is_active=True, is_superadmin=superadmin)
        if role:
            AdminBotMemberships.grant(id=f"mbr-{uuid.uuid4()}", admin_id=a.id, bot_id=bot.id, role=role)
        return a

    app = FastAPI()
    app.include_router(admin_widget.router, prefix="/api/admin")
    state: dict = {}
    app.dependency_overrides[get_admin] = lambda: state["admin"]

    def get_qr(a):
        state["admin"] = a
        return TestClient(app).get(f"/api/admin/bots/{bot.slug}/preview-qr.svg")

    return {"admin": admin, "get_qr": get_qr}


def test_non_member_is_forbidden(env):
    r = env["get_qr"](env["admin"]())
    assert r.status_code == 403


@pytest.mark.parametrize("role", ["viewer", "admin", "owner"])
def test_members_get_the_qr(env, role):
    r = env["get_qr"](env["admin"](role))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")


def test_superadmin_gets_the_qr(env):
    assert env["get_qr"](env["admin"](superadmin=True)).status_code == 200
