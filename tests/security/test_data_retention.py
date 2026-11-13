"""D4: visitor erasure, per-bot retention, nightly housekeeping, bot purge."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services import data_retention

OLD = datetime.utcnow() - timedelta(days=90)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _bot(**config):
    from models.bots import Bots

    return Bots.insert(id=_uid("bot"), slug=_uid("r"), name="R", config_json=config or None)


def _visitor(bot_id: str, user_id: str, *, email: str | None = None, at: datetime | None = None) -> dict:
    """Give ``user_id`` a full footprint on ``bot_id``; returns its ids."""
    from models.bot_channels import BotChannels
    from models.emly_files import EMLYFiles, Emly_Files
    from models.emly_messages import EMLYMessage, EMLYMessages
    from models.emly_sessions import EMLYSession
    from models.emly_user_action import EMLYUserActions, USER_ACTIONS
    from models.emly_user_channel_identities import EMLYUserChannelIdentities
    from models.emly_users import EMLYUser, EMLYUsers
    from models.otp_auth import OtpAuth, OtpAuthorization

    EMLYUsers.insert_new_user(bot_id=bot_id, id=user_id, first_name="Ann", last_name=None, email=email,
                              phone=None, ip="", browser="", meta=None)
    session = _uid("sess")
    EMLYMessages.insert_new_message(bot_id=bot_id, user_id=user_id, session_id=session, role="user",
                                    message="my secret question", not_useful=False, expanded_query=None, page="p")
    USER_ACTIONS.insert_new_action(bot_id=bot_id, user_id=user_id, action_name="form",
                                   action_value="contact", session_id=session, action_payload={"phone": "555"})
    f = Emly_Files.insert_new_file(bot_id=bot_id, user_id=user_id, file_name="id.png", file_type="image/png",
                                   file_size=3, size_bytes=3, mime_type="image/png")
    folder = data_retention.attachments_root() / user_id / f["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "id.png").write_bytes(b"png")
    channel = BotChannels.insert(id=_uid("ch"), bot_id=bot_id, type="telegram")
    EMLYUserChannelIdentities.insert(id=_uid("ident"), bot_id=bot_id, channel_id=channel.id,
                                     external_id=_uid("tg"), emly_user_id=user_id)
    OtpAuthorization.upsert_otp(bot_id=bot_id, user_id=email or user_id, otp_type="email",
                                otp_hash="h", expires_in=5)
    if at is not None:  # backdate everything
        EMLYUser.update(created_at=at, updated_at=at).where(
            (EMLYUser.bot == bot_id) & (EMLYUser.id == user_id)).execute()
        EMLYMessage.update(created_at=at).where(EMLYMessage.session_id == session).execute()
        EMLYSession.update(started_at=at, last_activity_at=at).where(EMLYSession.id == session).execute()
        EMLYUserActions.update(created_at=at).where(EMLYUserActions.session_id == session).execute()
        EMLYFiles.update(created_at=at).where(EMLYFiles.id == f["id"]).execute()
        OtpAuth.update(created_at=at, expires_at=at).where(OtpAuth.bot == bot_id).execute()
    return {"session": session, "file": f["id"], "folder": folder}


def _footprint(bot_id: str, user_id: str, email: str | None = None) -> dict:
    from models.emly_files import EMLYFiles
    from models.emly_messages import EMLYMessage
    from models.emly_sessions import EMLYSession
    from models.emly_user_action import EMLYUserActions
    from models.emly_user_channel_identities import EMLYUserChannelIdentity
    from models.emly_users import EMLYUser
    from models.otp_auth import OtpAuth

    return {
        "profile": EMLYUser.select().where((EMLYUser.bot == bot_id) & (EMLYUser.id == user_id)).count(),
        "messages": EMLYMessage.select().where((EMLYMessage.bot == bot_id) & (EMLYMessage.user_id == user_id)).count(),
        "sessions": EMLYSession.select().where((EMLYSession.bot_id == bot_id) & (EMLYSession.user_id == user_id)).count(),
        "submissions": EMLYUserActions.select().where(
            (EMLYUserActions.bot == bot_id) & (EMLYUserActions.user == user_id)).count(),
        "attachments": EMLYFiles.select().where((EMLYFiles.bot == bot_id) & (EMLYFiles.user == user_id)).count(),
        "identities": EMLYUserChannelIdentity.select().where(
            (EMLYUserChannelIdentity.bot == bot_id) & (EMLYUserChannelIdentity.emly_user == user_id)).count(),
        "otps": OtpAuth.select().where((OtpAuth.bot == bot_id) & OtpAuth.user_id.in_([user_id, email or user_id])).count(),
    }


EMPTY = {k: 0 for k in ("profile", "messages", "sessions", "submissions", "attachments", "identities", "otps")}


# ----------------------------------------------------------------- erasure


def test_erase_removes_only_that_visitor_on_that_bot(isolated_db):
    a, b = _bot(), _bot()
    # The widget's visitor id is per browser, so the same id can exist on
    # two bots; erasing it on one must not touch the other.
    target = _visitor(a.id, "v1", email="ann@example.com")
    same_id_other_bot = _visitor(b.id, "v1", email="ann@example.com")
    neighbour = _visitor(a.id, "v2")

    counts = data_retention.erase_end_user(a.id, "v1")

    assert _footprint(a.id, "v1", "ann@example.com") == EMPTY
    assert not target["folder"].exists()
    assert counts["messages"] == 1 and counts["profile"] == 1 and counts["attachment_files"] == 1
    assert all(v == 1 for v in _footprint(b.id, "v1", "ann@example.com").values())
    assert same_id_other_bot["folder"].exists()
    assert all(v == 1 for v in _footprint(a.id, "v2").values())
    assert neighbour["folder"].exists()


@pytest.fixture
def erase_api(isolated_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers
    from routes import admin_bot_dashboard
    from services.auth.dependencies import get_admin

    bot = _bot()
    current = {}
    app = FastAPI()
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: current["admin"]

    def as_role(role: str):
        admin = AdminUsers.insert(id=_uid("adm"), email=f"{_uid('a')}@example.com", is_active=True,
                                  is_superadmin=False)
        AdminBotMemberships.grant(id=_uid("mbr"), admin_id=admin.id, bot_id=bot.id, role=role)
        current["admin"] = admin
        return admin

    return TestClient(app), bot, as_role


def test_erase_endpoint_owner_only_and_audited(erase_api):
    from models.admin_audit_log import AdminAuditLog

    client, bot, as_role = erase_api
    _visitor(bot.id, "v1")
    url = f"/api/admin/bots/{bot.slug}/end-users/v1"

    as_role("admin")
    assert client.delete(url).status_code == 403
    assert _footprint(bot.id, "v1")["messages"] == 1

    owner = as_role("owner")
    r = client.delete(url)
    assert r.status_code == 200, r.text
    assert r.json()["erased"]["messages"] == 1
    row = AdminAuditLog.get(AdminAuditLog.action == "end_user.erased")
    assert (row.admin_id, row.bot_id, row.target_id) == (owner.id, bot.id, "v1")
    assert "secret" not in str(row.payload)  # counts only

    assert client.delete(url).status_code == 404


def test_erase_forgets_in_memory_conversation():
    from agents.conversation_agent import ConversationSessionManager

    mgr = ConversationSessionManager.__new__(ConversationSessionManager)
    mgr.sessions = {"b_v1_s1": 1, "b_v1_s2": 2, "b_v10_s1": 3, "c_v1_s1": 4}
    mgr._session_locks = {"b_v1_s1": 1}
    mgr.forget_user("b", "v1")
    assert set(mgr.sessions) == {"b_v10_s1", "c_v1_s1"} and mgr._session_locks == {}


# --------------------------------------------------------------- retention


def test_retention_purges_old_and_keeps_recent(isolated_db):
    bot = _bot()
    old = _visitor(bot.id, "old-visitor", at=OLD)
    _visitor(bot.id, "new-visitor")

    counts = data_retention.purge_bot_conversations(bot.id, 30)

    assert _footprint(bot.id, "old-visitor") == EMPTY
    assert not old["folder"].exists()
    assert counts["profiles"] == 1 and counts["messages"] == 1
    fresh = _footprint(bot.id, "new-visitor")
    assert all(v == 1 for v in fresh.values()), fresh


def test_retention_keeps_profile_with_recent_activity(isolated_db):
    from models.emly_messages import EMLYMessages

    bot = _bot()
    _visitor(bot.id, "returning", at=OLD)
    EMLYMessages.insert_new_message(bot_id=bot.id, user_id="returning", session_id=_uid("sess"), role="user",
                                    message="back again", not_useful=False, expanded_query=None, page="p")

    data_retention.purge_bot_conversations(bot.id, 30)

    left = _footprint(bot.id, "returning")
    assert left["profile"] == 1 and left["messages"] == 1  # only the recent one
    assert left["submissions"] == 0 and left["attachments"] == 0


def test_retention_days_validated():
    from pydantic import ValidationError

    from services.bot_config import LimitsConfig

    assert LimitsConfig(retention_days=30).retention_days == 30
    assert LimitsConfig().retention_days is None
    for bad in (0, -1, 3651):
        with pytest.raises(ValidationError):
            LimitsConfig(retention_days=bad)


def test_nightly_job_applies_bot_setting_and_housekeeping(isolated_db, monkeypatch):
    from models.otp_auth import OtpAuth
    from models.webhook_event_dedupe import WebhookEventDedupe

    with_retention = _bot(limits={"retention_days": 30})
    without = _bot()
    _visitor(with_retention.id, "v", at=OLD)
    _visitor(without.id, "v", at=OLD)
    WebhookEventDedupe.create(channel_type="slack", event_id=_uid("ev"),
                              received_at=datetime.utcnow() - timedelta(days=3), status="done")
    WebhookEventDedupe.create(channel_type="slack", event_id=_uid("ev"),
                              received_at=datetime.utcnow(), status="done")
    monkeypatch.delenv("WEBHOOK_DEDUPE_RETENTION_HOURS", raising=False)

    summary = data_retention.purge_expired_data()

    assert _footprint(with_retention.id, "v") == {**EMPTY, "otps": 0}
    kept = _footprint(without.id, "v")
    assert kept["messages"] == 1 and kept["profile"] == 1
    # Housekeeping is workspace-wide: the other bot's expired OTP goes too.
    assert OtpAuth.select().where(OtpAuth.bot == without.id).count() == 0
    assert summary["otps"] >= 1
    assert summary["webhook_dedupe"] >= 1
    assert WebhookEventDedupe.select().where(
        WebhookEventDedupe.received_at > datetime.utcnow() - timedelta(hours=1)).count() >= 1


def test_live_otp_survives_housekeeping(isolated_db):
    from models.otp_auth import OtpAuth, OtpAuthorization

    bot = _bot()
    OtpAuthorization.upsert_otp(bot_id=bot.id, user_id="a@example.com", otp_type="email", otp_hash="h",
                                expires_in=5)
    data_retention.purge_expired_otps()
    assert OtpAuth.select().where(OtpAuth.bot == bot.id).count() == 1


# --------------------------------------------------------------- bot purge


def test_bot_purge_leaves_no_rows_or_files(isolated_db, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from config import DATA_DIR
    from db.db import DB
    from models.bot_config_history import BotConfigHistory
    from models.bots import Bots
    from models.crawl_jobs import CrawlJob, CrawlJobPage
    from services import bot_purge

    monkeypatch.setattr(bot_purge, "get_rag_manager", lambda: SimpleNamespace(delete_bot=lambda *_: None))
    bot = _bot()
    visitor = _visitor(bot.id, "v1")
    uploads = Path(DATA_DIR) / "bots" / bot.id / "uploads" / "f1"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "doc.txt").write_text("x")
    now = datetime.utcnow()
    BotConfigHistory.create(id=_uid("h"), bot_id=bot.id, config_json="{}", config_version=1, created_at=now)
    job = CrawlJob.create(id=_uid("job"), bot_id=bot.id, seed_url="https://example.com", options_json="{}",
                          created_at=now, updated_at=now)
    CrawlJobPage.create(job_id=job.id, url="https://example.com", created_at=now, updated_at=now)

    bot_purge.purge_one(Bots.get_by_id(bot.id))

    assert not visitor["folder"].exists()
    assert not (Path(DATA_DIR) / "bots" / bot.id).exists()
    assert CrawlJobPage.select().where(CrawlJobPage.job_id == job.id).count() == 0
    leftovers = {}
    for (table,) in DB.execute_sql("select name from sqlite_master where type='table'").fetchall():
        if table == "admin_audit_log":
            continue  # kept on purpose; has its own retention
        cols = [r[1] for r in DB.execute_sql(f'PRAGMA table_info("{table}")').fetchall()]
        col = "bot_id" if "bot_id" in cols else ("id" if table == "bots" else None)
        if col:
            n = DB.execute_sql(f'select count(*) from "{table}" where "{col}" = ?', (bot.id,)).fetchone()[0]
            if n:
                leftovers[table] = n
    assert leftovers == {}
