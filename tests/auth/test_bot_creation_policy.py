"""C2: who may create bots — BOT_CREATION=writers (default) | any | superadmin."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app_with(isolated_db):
    from models.admin_users import AdminUserModel
    from routes import admin_bots, auth as auth_routes
    from services.auth.dependencies import get_admin

    app = FastAPI()
    app.include_router(admin_bots.router, prefix="/api/admin")
    app.include_router(auth_routes.router, prefix="/api/admin")
    state: dict = {}
    app.dependency_overrides[get_admin] = lambda: state["admin"]

    def as_admin(admin: AdminUserModel) -> TestClient:
        state["admin"] = admin
        return TestClient(app)

    return as_admin


def _admin(superadmin=False):
    from models.admin_users import AdminUsers

    return AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@x.example",
                             is_active=True, is_superadmin=superadmin)


def _member(admin, role):
    from models.admin_bot_memberships import AdminBotMemberships
    from models.bots import Bots

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"m-{uuid.uuid4().hex[:6]}", name="M")
    AdminBotMemberships.grant(id=f"mbr-{uuid.uuid4()}", admin_id=admin.id, bot_id=bot.id, role=role)
    return bot


def _create(client) -> int:
    slug = f"new-{uuid.uuid4().hex[:6]}"
    return client.post("/api/admin/bots", json={"slug": slug, "name": "New"}).status_code


# who: superadmin | owner | admin | viewer | none
CASES = {
    "writers": {"superadmin": True, "owner": True, "admin": True, "viewer": False, "none": False},
    "any": {"superadmin": True, "owner": True, "admin": True, "viewer": True, "none": True},
    "superadmin": {"superadmin": True, "owner": False, "admin": False, "viewer": False, "none": False},
}


@pytest.mark.parametrize("policy", list(CASES))
@pytest.mark.parametrize("who", ["superadmin", "owner", "admin", "viewer", "none"])
def test_bot_creation_policy(app_with, monkeypatch, policy, who):
    monkeypatch.setenv("BOT_CREATION", policy)
    admin = _admin(superadmin=(who == "superadmin"))
    if who in ("owner", "admin", "viewer"):
        _member(admin, who)
    client = app_with(admin)
    allowed = CASES[policy][who]
    status = _create(client)
    assert status == (201 if allowed else 403), status
    assert client.get("/api/admin/auth/me").json()["can_create_bots"] is allowed


def test_default_policy_is_writers(app_with, monkeypatch):
    from services.authz import bot_creation_policy

    monkeypatch.delenv("BOT_CREATION", raising=False)
    assert bot_creation_policy() == "writers"
    viewer = _admin()
    _member(viewer, "viewer")
    r = app_with(viewer).post("/api/admin/bots", json={"slug": "v-bot", "name": "V"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "bot_creation_not_allowed"


def test_unknown_policy_falls_back_to_writers(monkeypatch):
    from services.authz import bot_creation_policy

    monkeypatch.setenv("BOT_CREATION", "everyone-please")
    assert bot_creation_policy() == "writers"


def test_duplicate_is_also_gated(app_with, monkeypatch):
    monkeypatch.setenv("BOT_CREATION", "writers")
    viewer = _admin()
    src = _member(viewer, "viewer")
    r = app_with(viewer).post("/api/admin/bots", json={"slug": "dup-x", "name": "D", "source_bot_id": src.id})
    assert r.status_code == 403
