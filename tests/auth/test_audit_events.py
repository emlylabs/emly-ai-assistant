"""C7 (part 2): the newly audited events write a row with actor, IP, user
agent and outcome; config/role changes carry a masked before/after diff."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

UA = "audit-test-agent/1.0"


def _row(action):
    from models.admin_audit_log import AdminAuditLogs

    rows = AdminAuditLogs.list_filtered(action=action)
    assert len(rows) == 1, f"expected one {action} row, got {len(rows)}"
    return rows[0]


def _assert_meta(row, actor):
    assert row.admin_id == actor.id
    assert row.ip == "testclient"
    assert row.ua == UA
    assert row.success is True


@pytest.fixture
def env(isolated_db, monkeypatch):
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from routes import admin_admins, admin_bot_dashboard, admin_bot_files
    from services.audit import AuditRequestContextMiddleware
    from services.auth.dependencies import get_admin

    monkeypatch.setattr(admin_bot_files, "_kick_embed", lambda *a, **k: None)
    fake_rag = SimpleNamespace(delete_file=lambda **kw: None, delete_bot=lambda *a: None)
    monkeypatch.setattr(admin_bot_files, "get_rag_manager", lambda: fake_rag)

    actor = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="root@x.example",
                              is_active=True, is_superadmin=True)
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"a-{uuid.uuid4().hex[:6]}", name="A",
                      config_json={"limits": {"max_file_size_mb": 5}})
    app = FastAPI()
    for r in (admin_bot_dashboard.router, admin_bot_files.router, admin_admins.router):
        app.include_router(r, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: actor
    app.add_middleware(AuditRequestContextMiddleware)
    client = TestClient(app, headers={"User-Agent": UA})
    return SimpleNamespace(client=client, actor=actor, bot=bot, base=f"/api/admin/bots/{bot.slug}")


# --------------------------------------------------------------- config


def test_config_save_is_audited_with_diff(env):
    r = env.client.put(f"{env.base}/config", json={"config": {"limits": {"max_file_size_mb": 7}}})
    assert r.status_code == 200, r.text
    row = _row("config.saved")
    _assert_meta(row, env.actor)
    assert row.payload["changed"]["limits.max_file_size_mb"] == {"before": 5, "after": 7}
    assert row.payload["forced"] is False


def test_draft_publish_and_discard_are_audited(env):
    env.client.put(f"{env.base}/config/draft", json={"config": {"limits": {"max_file_size_mb": 9}}})
    r = env.client.post(f"{env.base}/config/draft/publish")
    assert r.status_code == 200, r.text
    row = _row("config.draft.published")
    _assert_meta(row, env.actor)
    assert row.payload["changed"]["limits.max_file_size_mb"]["after"] == 9

    env.client.put(f"{env.base}/config/draft", json={"config": {"limits": {"max_file_size_mb": 11}}})
    assert env.client.delete(f"{env.base}/config/draft").status_code == 200
    row = _row("config.draft.discarded")
    _assert_meta(row, env.actor)
    assert row.payload == {"had_draft": True}


def test_history_restore_is_audited(env):
    from models.bot_config_history import BotConfigHistories

    env.client.put(f"{env.base}/config", json={"config": {"limits": {"max_file_size_mb": 7}}})
    env.client.put(f"{env.base}/config", json={"config": {"limits": {"max_file_size_mb": 8}}})
    oldest = BotConfigHistories.list_for_bot(env.bot.id)[-1]
    r = env.client.post(f"{env.base}/config/history/{oldest.id}/restore")
    assert r.status_code == 200, r.text
    row = _row("config.history.restored")
    _assert_meta(row, env.actor)
    assert row.payload["history_entry_id"] == oldest.id
    assert row.payload["changed"]["limits.max_file_size_mb"] == {"before": 8, "after": 7}


def test_api_key_set_and_clear_are_audited_without_value(env):
    secret = "sk-live-super-secret-value"
    assert env.client.put(f"{env.base}/api-key", json={"api_key": secret}).status_code == 204
    row = _row("bot.api_key.set")
    _assert_meta(row, env.actor)
    assert secret not in repr(row.payload)
    assert env.client.put(f"{env.base}/api-key", json={"api_key": None}).status_code == 204
    _assert_meta(_row("bot.api_key.cleared"), env.actor)


def test_config_diff_masks_secret_paths():
    from services.audit_diff import config_diff

    diff = config_diff({"llm": {"api_key": "old-key"}, "x": "a" * 900},
                       {"llm": {"api_key": "new-key"}, "x": "b" * 900})
    assert diff["changed"]["llm.api_key"] == {"before": "***", "after": "***"}
    assert "old-key" not in repr(diff) and "new-key" not in repr(diff)
    assert len(diff["changed"]["x"]["after"]) < 600  # truncated


# -------------------------------------------------------------- messages


def test_message_update_and_delete_are_audited(env):
    from models.emly_messages import EMLYMessages

    msg = EMLYMessages.insert_new_message(bot_id=env.bot.id, user_id="u", session_id="s", role="assistant",
                                          message="hello", not_useful=False, expanded_query=None, page=None)
    mid = msg["id"] if isinstance(msg, dict) else msg.id
    assert env.client.patch(f"{env.base}/messages/{mid}", json={"not_useful": True}).status_code == 200
    row = _row("message.updated")
    _assert_meta(row, env.actor)
    assert row.payload == {"not_useful": True}
    assert env.client.delete(f"{env.base}/messages/{mid}").status_code == 204
    _assert_meta(_row("message.deleted"), env.actor)


# ----------------------------------------------------------------- files


def test_file_upload_reindex_delete_are_audited(env):
    r = env.client.post(f"{env.base}/files", files={"file": ("notes.txt", b"hello world", "text/plain")})
    assert r.status_code == 202, r.text
    file_id = r.json()["id"]
    row = _row("file.uploaded")
    _assert_meta(row, env.actor)
    assert row.target_id == file_id and row.payload["file_name"] == "notes.txt"

    assert env.client.post(f"{env.base}/files/{file_id}/reindex").status_code == 200
    _assert_meta(_row("file.reindexed"), env.actor)
    assert env.client.post(f"{env.base}/reindex").status_code == 202
    _assert_meta(_row("files.reindexed_all"), env.actor)
    assert env.client.delete(f"{env.base}/files/{file_id}").status_code == 204
    _assert_meta(_row("file.deleted"), env.actor)


# ----------------------------------------------------------------- roles


def test_admin_update_records_before_after(env):
    from models.admin_users import AdminUsers

    target = AdminUsers.insert(id="adm-target", email="t@x.example", is_active=True, is_superadmin=False)
    r = env.client.patch(f"/api/admin/admins/{target.id}", json={"is_superadmin": True})
    assert r.status_code == 200, r.text
    row = _row("admin.updated")
    _assert_meta(row, env.actor)
    assert row.payload["changed"] == {"is_superadmin": {"before": False, "after": True}}
