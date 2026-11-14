"""PR #11: a zero-byte upload is rejected with 400 and leaves nothing
behind — no per-file directory on disk and no ``emly_files`` row — the
same cleanup the oversize path does."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def upload_env(isolated_db, monkeypatch, tmp_path):
    from models.admin_users import AdminUsers
    from models.bots import Bot, Bots
    from models.emly_files import EMLYFiles
    from routes import admin_bot_files
    from services.auth.dependencies import get_admin

    monkeypatch.setattr(admin_bot_files, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(admin_bot_files, "_kick_embed", lambda *a, **k: None)

    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="up@x.example",
                              is_active=True, is_superadmin=True)
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"up-{uuid.uuid4().hex[:6]}", name="Up",
                      config_json={"limits": {"max_file_size_mb": 1}})
    app = FastAPI()
    app.include_router(admin_bot_files.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: admin

    uploads = tmp_path / "bots" / bot.id / "uploads"
    yield TestClient(app), bot, uploads

    EMLYFiles.delete().where(EMLYFiles.bot == bot.id).execute()
    Bot.delete().where(Bot.id == bot.id).execute()


def _file_rows(bot_id: str):
    from models.emly_files import EMLYFiles

    return list(EMLYFiles.select().where(EMLYFiles.bot == bot_id))


def _leftovers(uploads):
    return sorted(p.relative_to(uploads) for p in uploads.rglob("*")) if uploads.exists() else []


def test_empty_upload_is_rejected_and_cleaned_up(upload_env):
    client, bot, uploads = upload_env
    r = client.post(
        f"/api/admin/bots/{bot.slug}/files",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "The file is empty, so there is nothing to index."
    assert _leftovers(uploads) == []
    assert _file_rows(bot.id) == []


def test_one_byte_upload_is_accepted(upload_env):
    """Control: the empty-file guard is size == 0, not "small"."""
    client, bot, uploads = upload_env
    r = client.post(
        f"/api/admin/bots/{bot.slug}/files",
        files={"file": ("one.txt", b"x", "text/plain")},
    )
    assert r.status_code == 202, r.text
    file_id = r.json()["id"]
    assert r.json()["size_bytes"] == 1
    assert (uploads / file_id / "one.txt").read_bytes() == b"x"
    assert [str(row.id) for row in _file_rows(bot.id)] == [file_id]
