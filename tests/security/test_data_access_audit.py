"""G5e: AUDIT_DATA_ACCESS records who read conversations and visitor details."""
from __future__ import annotations

import json
import uuid

import pytest


def _uid(p: str) -> str:
    return f"{p}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def api(isolated_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import main
    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from models.emly_messages import EMLYMessages
    from models.emly_users import EMLYUsers
    from routes import admin_bot_dashboard, admin_bot_sessions
    from services import data_access_audit
    from services.audit import AuditRequestContextMiddleware, AuditUnavailable
    from services.auth.dependencies import get_admin

    data_access_audit.reset_for_tests()
    bot = Bots.insert(id=_uid("bot"), slug=_uid("da"), name="DA")
    viewer = AdminUsers.insert(id=_uid("adm"), email=f"{_uid('v')}@example.com", is_active=True,
                               is_superadmin=False)
    AdminBotMemberships.grant(id=_uid("mbr"), admin_id=viewer.id, bot_id=bot.id, role="viewer")
    EMLYUsers.insert_new_user(bot_id=bot.id, id="vis-1", first_name="Ann", last_name=None,
                              email="ann@example.com", phone=None, ip="", browser="", meta=None)
    session = _uid("sess")
    msg = EMLYMessages.insert_new_message(bot_id=bot.id, user_id="vis-1", session_id=session, role="user",
                                          message="my payslip is wrong", not_useful=False,
                                          expanded_query=None, page="p")

    app = FastAPI()
    app.include_router(admin_bot_sessions.router, prefix="/api/admin")
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.add_middleware(AuditRequestContextMiddleware)
    app.add_exception_handler(AuditUnavailable, main.app.exception_handlers[AuditUnavailable])
    app.dependency_overrides[get_admin] = lambda: viewer
    yield TestClient(app), bot, viewer, session, msg
    data_access_audit.reset_for_tests()


def _reads(bot_id):
    from models.admin_audit_log import AdminAuditLog

    rows = list(AdminAuditLog.select().where(
        (AdminAuditLog.bot_id == bot_id) & (AdminAuditLog.action == "data.read")).order_by(AdminAuditLog.id))
    for r in rows:
        if isinstance(r.payload, str):
            r.payload = json.loads(r.payload)
    return rows


def _message_id(msg):
    return msg["id"] if isinstance(msg, dict) else msg.id


def test_off_by_default(api, monkeypatch):
    client, bot, _, session, _ = api
    monkeypatch.delenv("AUDIT_DATA_ACCESS", raising=False)
    assert client.get(f"/api/admin/bots/{bot.slug}/sessions/{session}").status_code == 200
    assert _reads(bot.id) == []


def test_each_read_is_recorded_without_content(api, monkeypatch):
    client, bot, viewer, session, msg = api
    monkeypatch.setenv("AUDIT_DATA_ACCESS", "true")
    base = f"/api/admin/bots/{bot.slug}"
    for path in (
        f"{base}/sessions?q=payslip",
        f"{base}/sessions/{session}",
        f"{base}/messages?session_id={session}",
        f"{base}/messages/{_message_id(msg)}",
        f"{base}/end-users",
        f"{base}/end-users/vis-1",
    ):
        assert client.get(path).status_code == 200, path

    rows = _reads(bot.id)
    got = {(r.payload["what"], r.target_type, r.target_id) for r in rows}
    assert len(rows) == 6
    assert got == {
        ("conversation_list", None, None),
        ("conversation", "session", session),
        ("message_list", None, None),
        ("message", "message", str(_message_id(msg))),
        ("visitor_list", None, None),
        ("visitor", "end_user", "vis-1"),
    }
    assert all(r.admin_id == viewer.id for r in rows)
    (listing,) = [r for r in rows if r.payload["what"] == "conversation_list"]
    assert listing.payload["filters"] == ["search"]
    assert listing.payload["route"] == "/api/admin/bots/{slug}/sessions"
    dumped = repr([r.payload for r in rows])
    assert "payslip" not in dumped and "ann@example.com" not in dumped


def test_repeat_reads_are_rate_limited_per_target(api, monkeypatch):
    client, bot, _, session, _ = api
    monkeypatch.setenv("AUDIT_DATA_ACCESS", "true")
    for _ in range(3):
        client.get(f"/api/admin/bots/{bot.slug}/sessions/{session}")
        client.get(f"/api/admin/bots/{bot.slug}/sessions?skip=0")
    assert len(_reads(bot.id)) == 2


def test_not_found_and_denied_reads_write_no_data_row(api, monkeypatch):
    client, bot, viewer, _, _ = api
    from models.admin_bot_memberships import AdminBotMemberships

    monkeypatch.setenv("AUDIT_DATA_ACCESS", "true")
    assert client.get(f"/api/admin/bots/{bot.slug}/sessions/nope").status_code == 404
    AdminBotMemberships.revoke(viewer.id, bot.id)
    assert client.get(f"/api/admin/bots/{bot.slug}/end-users").status_code == 403
    assert _reads(bot.id) == []


def test_unwritable_row_fails_the_read_when_fail_closed(api, monkeypatch):
    from models.admin_audit_log import AdminAuditLogs

    client, bot, _, session, _ = api
    monkeypatch.setenv("AUDIT_DATA_ACCESS", "true")
    monkeypatch.setenv("AUDIT_FAIL_CLOSED", "true")
    monkeypatch.setattr(AdminAuditLogs, "insert", lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    assert client.get(f"/api/admin/bots/{bot.slug}/sessions/{session}").status_code == 503
    # No window was opened by the failed write.
    assert client.get(f"/api/admin/bots/{bot.slug}/sessions/{session}").status_code == 503
