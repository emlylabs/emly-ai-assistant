"""G13c: upload size caps hold before anything large is stored or read.

- The unauthenticated widget ``action`` route had no cap on the request
  body: Starlette spools the whole multipart body before the route can
  check a file, so any size was accepted onto disk first.
- Widget attachments were read into memory whole.
- Admin uploads checked the request's Content-Length and then copied the
  whole file before checking its real size.
"""
from __future__ import annotations

import asyncio
import io
import uuid

import pytest
from fastapi import FastAPI, Request, UploadFile
from fastapi.testclient import TestClient

from services.request_limits import WidgetBodyLimitMiddleware


@pytest.fixture
def limited_app(monkeypatch):
    monkeypatch.setenv("WIDGET_MAX_REQUEST_MB", "1")
    seen = []
    app = FastAPI()

    @app.post("/widget/{ref}/action")
    async def action(request: Request):
        body = await request.body()
        seen.append(len(body))
        return {"ok": True}

    @app.post("/api/admin/other")
    async def other(request: Request):
        return {"len": len(await request.body())}

    app.add_middleware(WidgetBodyLimitMiddleware)
    return TestClient(app), seen


MB = 1024 * 1024


def test_declared_oversize_body_is_refused_before_the_route(limited_app):
    client, seen = limited_app
    r = client.post("/widget/b/action", content=b"x" * (MB + 1))
    assert r.status_code == 413
    assert seen == []


def test_streamed_oversize_body_is_refused_as_it_arrives(limited_app):
    client, seen = limited_app

    def chunks():  # no Content-Length: chunked transfer
        for _ in range(3):
            yield b"x" * (MB // 2)

    r = client.post("/widget/b/action", content=chunks())
    assert r.status_code == 413
    assert seen == []


def test_body_under_the_cap_passes(limited_app):
    client, seen = limited_app
    assert client.post("/widget/b/action", content=b"x" * 1000).status_code == 200
    assert seen == [1000]


def test_other_paths_are_not_limited(limited_app):
    client, _ = limited_app
    assert client.post("/api/admin/other", content=b"x" * (MB + 1)).json() == {"len": MB + 1}


def test_default_matches_the_existing_hard_limits(monkeypatch):
    from routes.widget import _ABSOLUTE_BYTE_CAP, _PER_REQUEST_FILE_CAP
    from services.request_limits import widget_max_request_bytes

    monkeypatch.delenv("WIDGET_MAX_REQUEST_MB", raising=False)
    assert widget_max_request_bytes() == _PER_REQUEST_FILE_CAP * _ABSOLUTE_BYTE_CAP


def test_app_installs_the_middleware():
    import main

    assert any(m.cls is WidgetBodyLimitMiddleware for m in main.app.user_middleware)


# ------------------------------------------------------------ widget attachments
def test_widget_attachment_is_copied_in_chunks(isolated_db, tmp_path, monkeypatch):
    from models.bots import Bots
    from services import widget_uploads

    monkeypatch.setattr(widget_uploads, "DATA_DIR", str(tmp_path))
    from models.emly_users import EMLYUsers

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"u-{uuid.uuid4().hex[:6]}", name="U")
    EMLYUsers.insert_new_user(bot_id=bot.id, id="v1", first_name="A", last_name=None, email="",
                              phone="", ip="", browser="", meta=None)
    data = b"y" * (3 * MB + 5)
    upload = UploadFile(file=io.BytesIO(data), filename="big.pdf", size=len(data))
    reads = []
    real_read = upload.read

    async def spy(size=-1):
        reads.append(size)
        return await real_read(size)

    upload.read = spy
    (saved,) = asyncio.run(widget_uploads.handle_file_uploads([upload], "v1", bot_id=bot.id))

    assert saved["size"] == len(data)
    assert open(saved["path"], "rb").read() == data
    assert reads and all(0 < s <= MB for s in reads)  # never one whole-file read


# ------------------------------------------------------------ admin uploads
def test_admin_upload_over_cap_is_refused_before_copying(isolated_db, monkeypatch, tmp_path):
    """Regression guard: an oversize file is refused before the copy loop.
    (The route now checks the part's real size, so this also holds when a
    client sends no or a wrong Content-Length; TestClient always sends it.)"""
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from routes import admin_bot_files
    from services.auth.dependencies import get_admin

    monkeypatch.setattr(admin_bot_files, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(admin_bot_files, "_kick_embed", lambda *a, **k: None)
    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="cap@x.example", is_active=True,
                              is_superadmin=True)
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"cap-{uuid.uuid4().hex[:6]}", name="Cap",
                      config_json={"limits": {"max_file_size_mb": 1}})
    app = FastAPI()
    app.include_router(admin_bot_files.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: admin
    copied = []
    monkeypatch.setattr(admin_bot_files.hashlib, "sha256", lambda: copied.append(1) or __import__("hashlib").sha256())

    r = TestClient(app).post(f"/api/admin/bots/{bot.slug}/files",
                             files={"file": ("big.txt", b"z" * (2 * MB), "text/plain")})

    assert r.status_code == 413
    assert copied == []  # refused before the copy loop started
    uploads = tmp_path / "bots" / bot.id / "uploads"
    assert not uploads.exists() or not any(uploads.rglob("*"))
