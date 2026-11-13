"""C4: taking over a session another admin holds needs force + owner."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def env(isolated_db):
    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from models.emly_sessions import EMLYSessions
    from routes import admin_bot_sessions
    from services.auth.dependencies import get_admin

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"t-{uuid.uuid4().hex[:6]}", name="T")
    session_id = f"session-{uuid.uuid4()}"
    EMLYSessions.upsert_on_message(bot_id=bot.id, session_id=session_id, user_id="visitor",
                                   channel_id=None, ts=datetime.now(timezone.utc))

    def admin(role):
        a = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@x.example",
                              is_active=True, is_superadmin=False)
        AdminBotMemberships.grant(id=f"mbr-{uuid.uuid4()}", admin_id=a.id, bot_id=bot.id, role=role)
        return a

    app = FastAPI()
    app.include_router(admin_bot_sessions.router, prefix="/api/admin")
    state: dict = {}
    app.dependency_overrides[get_admin] = lambda: state["admin"]
    client = TestClient(app)

    def takeover(a, force=False):
        state["admin"] = a
        url = f"/api/admin/bots/{bot.slug}/sessions/{session_id}/takeover"
        return client.post(url + ("?force=true" if force else ""))

    def holder():
        return EMLYSessions.get(bot.id, session_id).taken_over_by

    return {"bot": bot, "admin": admin, "takeover": takeover, "holder": holder, "session_id": session_id}


def test_free_session_can_be_claimed(env):
    a = env["admin"]("admin")
    assert env["takeover"](a).status_code == 200
    assert env["holder"]() == a.id


def test_reclaiming_own_session_is_fine(env):
    a = env["admin"]("admin")
    env["takeover"](a)
    assert env["takeover"](a).status_code == 200


def test_held_session_returns_409_without_force(env):
    first, second = env["admin"]("admin"), env["admin"]("owner")
    env["takeover"](first)
    r = env["takeover"](second)
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "session_held", "held_by": first.id}
    assert env["holder"]() == first.id


def test_force_requires_owner(env):
    first, second = env["admin"]("admin"), env["admin"]("admin")
    env["takeover"](first)
    assert env["takeover"](second, force=True).status_code == 403
    assert env["holder"]() == first.id


def test_owner_can_force_and_it_is_audited(env):
    from models.admin_audit_log import AdminAuditLogs

    first, owner = env["admin"]("admin"), env["admin"]("owner")
    env["takeover"](first)
    r = env["takeover"](owner, force=True)
    assert r.status_code == 200
    assert env["holder"]() == owner.id
    rows = AdminAuditLogs.list_filtered(action="session.takeover.forced")
    assert len(rows) == 1
    assert rows[0].admin_id == owner.id
    assert rows[0].payload == {"previous_holder": first.id}
    assert rows[0].ip is not None




def test_claim_takeover_model_conditional_update(env):
    from models.emly_sessions import EMLYSessions

    bot_id = env["bot"].id
    sid = env["session_id"]
    assert EMLYSessions.claim_takeover(bot_id, sid, admin_id="adm-a") is True
    assert EMLYSessions.claim_takeover(bot_id, sid, admin_id="adm-b") is False
    assert EMLYSessions.claim_takeover(bot_id, sid, admin_id="adm-b", force=True) is True
    assert env["holder"]() == "adm-b"
