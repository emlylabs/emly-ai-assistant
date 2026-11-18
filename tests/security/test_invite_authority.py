"""S20: an invite is honoured only while its inviter still holds what it grants.

Accepting checked only the email, status and expiry. An owner about to be
removed could invite a second address as owner and regain the bot later;
a deleted admin's pending invite to their own address re-provisioned them
at their next sign-in.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _id(p):
    return f"{p}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def world(isolated_db):
    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from models.pending_admins import PendingAdmins
    from routes import admin_admins
    from services.auth.dependencies import get_admin

    bot = Bots.insert(id=_id("bot"), slug=_id("inv"), name="Inv")
    owner = AdminUsers.insert(id=_id("adm"), email=f"{_id('o')}@x.example", is_active=True)
    AdminBotMemberships.grant(_id("mbr"), owner.id, bot.id, "owner")
    root = AdminUsers.insert(id=_id("adm"), email=f"{_id('r')}@x.example", is_active=True, is_superadmin=True)
    invitee = AdminUsers.insert(id=_id("adm"), email=f"{_id('i')}@x.example", is_active=True)
    invite = PendingAdmins.create_space_invite(email=invitee.email, invited_by=owner.id,
                                               target_bot_id=bot.id, role="owner")
    current = {"admin": invitee}
    app = FastAPI()
    app.include_router(admin_admins.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: current["admin"]
    return {"bot": bot, "owner": owner, "root": root, "invitee": invitee, "invite": invite,
            "current": current, "client": TestClient(app)}


def _accept(w):
    return w["client"].post(f"/api/admin/admins/invites/{w['invite'].token}/accept")


def _membership(w):
    from models.admin_bot_memberships import AdminBotMemberships

    return AdminBotMemberships.get(w["invitee"].id, w["bot"].id)


def test_valid_invite_still_works(world):
    assert _accept(world).status_code == 200
    assert _membership(world).role == "owner"


def test_inviter_demoted_voids_the_invite(world):
    from models.admin_bot_memberships import AdminBotMemberships

    AdminBotMemberships.revoke(world["owner"].id, world["bot"].id)  # no longer an owner

    r = _accept(world)
    assert r.status_code == 410 and r.json()["detail"]["code"] == "invite_revoked"
    assert _membership(world) is None


def test_deleted_bot_voids_the_invite(world):
    from models.bots import Bots

    Bots.soft_delete(world["bot"].id)
    assert _accept(world).status_code == 410
    assert _membership(world) is None


def test_deleting_the_inviter_revokes_their_invites(world):
    from models.pending_admins import PendingAdmins

    world["current"]["admin"] = world["root"]
    r = world["client"].delete(f"/api/admin/admins/{world['owner'].id}")
    assert r.status_code == 204
    assert PendingAdmins.get_by_id(world["invite"].id).status == "revoked"


def test_deactivating_an_admin_revokes_invites_addressed_to_them(world):
    from models.pending_admins import PendingAdmins

    world["current"]["admin"] = world["root"]
    r = world["client"].patch(f"/api/admin/admins/{world['invitee'].id}", json={"is_active": False})
    assert r.status_code == 200
    assert PendingAdmins.get_by_id(world["invite"].id).status == "revoked"


def test_superadmin_invite_needs_a_superadmin_inviter(isolated_db):
    from models.admin_users import AdminUsers
    from models.pending_admins import PendingAdmins
    from services.auth.user_resolver import invite_authority_problem

    plain = AdminUsers.insert(id=_id("adm"), email=f"{_id('p')}@x.example", is_active=True)
    row = PendingAdmins.create(email="new@x.example", invited_by=plain.id, is_superadmin=True, bot_assignments=[])
    assert invite_authority_problem(row) == "inviter_not_superadmin"
