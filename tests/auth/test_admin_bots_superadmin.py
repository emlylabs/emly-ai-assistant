"""Superadmin-bypass regression: ``GET /api/admin/bots`` and per-bot routes
must work for a superadmin who has no explicit ``admin_bot_membership`` rows.

Pre-fix, ``list_bots`` only returned the admin's explicit memberships, so a
superadmin without auto-granted membership saw an empty list and 403'd on
every per-bot route.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def bot_admin_app(isolated_db, monkeypatch):
    from models.admin_users import AdminUserModel

    from routes import admin_bots
    from services.auth.dependencies import get_admin

    app = FastAPI()
    app.include_router(admin_bots.router, prefix="/api/admin")

    state: dict = {"admin": None}

    def _override_get_admin() -> AdminUserModel:
        from fastapi import HTTPException, status

        if state["admin"] is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "missing_token"})
        return state["admin"]

    app.dependency_overrides[get_admin] = _override_get_admin

    def set_admin(admin: AdminUserModel | None) -> None:
        state["admin"] = admin

    yield app, set_admin


@pytest.fixture
def two_bots(isolated_db):
    from models.bots import Bots

    bot_a = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"a-{uuid.uuid4().hex[:6]}", name="A")
    bot_b = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"b-{uuid.uuid4().hex[:6]}", name="B")
    return {"a": bot_a, "b": bot_b}


def test_list_bots_returns_all_bots_for_superadmin_without_memberships(bot_admin_app, two_bots, isolated_db):
    """A superadmin with no membership rows should see every active bot."""
    from models.admin_users import AdminUsers

    superadmin = AdminUsers.insert(
        id=f"adm-super-{uuid.uuid4().hex[:6]}",
        email="super@example.com",
        is_active=True,
        is_superadmin=True,
    )
    app, set_admin = bot_admin_app
    set_admin(superadmin)
    client = TestClient(app)

    r = client.get("/api/admin/bots")
    assert r.status_code == 200, r.text
    bot_ids = {b["id"] for b in r.json()}
    assert two_bots["a"].id in bot_ids
    assert two_bots["b"].id in bot_ids


def test_list_bots_returns_only_membership_for_regular_admin(bot_admin_app, two_bots, isolated_db):
    """A regular admin sees only the bots they're a member of."""
    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers

    admin = AdminUsers.insert(
        id=f"adm-{uuid.uuid4().hex[:6]}", email="alice@example.com", is_active=True
    )
    AdminBotMemberships.grant(
        id=f"mbr-{uuid.uuid4().hex[:8]}",
        admin_id=admin.id,
        bot_id=two_bots["a"].id,
        role="admin",
    )
    app, set_admin = bot_admin_app
    set_admin(admin)
    client = TestClient(app)

    r = client.get("/api/admin/bots")
    assert r.status_code == 200, r.text
    bot_ids = {b["id"] for b in r.json()}
    assert two_bots["a"].id in bot_ids
    assert two_bots["b"].id not in bot_ids


def test_get_single_bot_works_for_superadmin_without_membership(bot_admin_app, two_bots, isolated_db):
    from models.admin_users import AdminUsers

    superadmin = AdminUsers.insert(
        id=f"adm-super-{uuid.uuid4().hex[:6]}",
        email="super@example.com",
        is_active=True,
        is_superadmin=True,
    )
    app, set_admin = bot_admin_app
    set_admin(superadmin)
    client = TestClient(app)

    r = client.get(f"/api/admin/bots/{two_bots['b'].slug}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == two_bots["b"].id


def test_patch_single_bot_works_for_superadmin_without_membership(bot_admin_app, two_bots, isolated_db):
    from models.admin_users import AdminUsers

    superadmin = AdminUsers.insert(
        id=f"adm-super-{uuid.uuid4().hex[:6]}",
        email="super@example.com",
        is_active=True,
        is_superadmin=True,
    )
    app, set_admin = bot_admin_app
    set_admin(superadmin)
    client = TestClient(app)

    r = client.patch(
        f"/api/admin/bots/{two_bots['b'].slug}",
        json={"name": "B-renamed"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "B-renamed"


def test_regular_admin_403s_on_non_member_bot(bot_admin_app, two_bots, isolated_db):
    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers

    admin = AdminUsers.insert(
        id=f"adm-{uuid.uuid4().hex[:6]}", email="bob@example.com", is_active=True
    )
    AdminBotMemberships.grant(
        id=f"mbr-{uuid.uuid4().hex[:8]}",
        admin_id=admin.id,
        bot_id=two_bots["a"].id,
        role="owner",
    )
    app, set_admin = bot_admin_app
    set_admin(admin)
    client = TestClient(app)

    r = client.get(f"/api/admin/bots/{two_bots['b'].slug}")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "not_member"
