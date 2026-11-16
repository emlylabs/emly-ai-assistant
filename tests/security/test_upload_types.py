"""G13c: uploads are checked by their bytes, not the type the browser declares."""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient

from services.upload_types import UploadRejected, check_upload, sniff

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
EXE = b"MZ\x90\x00" + b"\x00" * 200
ELF = b"\x7fELF\x02\x01\x01" + b"\x00" * 200
GZIP = b"\x1f\x8b\x08\x00" + b"\x00" * 50
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


DOCX = _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": "<w:document/>"})
PLAIN_ZIP = _zip({"a.txt": "hello"})


def check(data: bytes, name: str, mime: str = "") -> str:
    return check_upload(io.BytesIO(data), name, mime)


@pytest.mark.parametrize("data,name,mime,kind", [
    (PDF, "policy.pdf", "application/pdf", "pdf"),
    (DOCX, "handbook.docx", DOCX_MIME, "ooxml"),
    (b"name,team\nA,HR\n", "staff.csv", "text/csv", "text"),
    # Windows browsers send .csv as application/vnd.ms-excel.
    (b"name,team\nA,HR\n", "staff.csv", "application/vnd.ms-excel", "text"),
    (b"\xff\xfeh\x00i\x00", "notes.txt", "text/plain", "text"),  # UTF-16 with BOM
    ("Grüße\n".encode(), "notes.md", "", "text"),
    (b"<html><body>x</body></html>", "page.html", "text/html", "text"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "logo.png", "image/png", "png"),
    (b"anything", "data.bin", "application/x-custom", "text"),  # unknown type: allowed
])
def test_real_files_pass(data, name, mime, kind):
    assert check(data, name, mime) == kind


@pytest.mark.parametrize("data,name,mime", [
    (EXE, "report.pdf", "application/pdf"),   # program renamed and re-typed
    (ELF, "notes.txt", "text/plain"),
    (EXE, "thing", ""),                       # no name or type at all
    (GZIP, "notes.txt", "text/plain"),
    (PLAIN_ZIP, "notes.txt", "text/plain"),
    (PLAIN_ZIP, "bundle.docx", DOCX_MIME),    # a zip that isn't an office file
    (b"PK\x03\x04garbage", "x.docx", DOCX_MIME),
    (b"hello", "run.exe", "application/octet-stream"),  # blocked by name
    (b"alert(1)", "invoice.js", "text/javascript"),
    (PDF, "notes.txt", "text/plain"),         # content isn't what it claims
    (b"plain text", "policy.pdf", "application/pdf"),
    (b"plain text", "handbook.docx", DOCX_MIME),
])
def test_disguised_or_dangerous_files_are_refused(data, name, mime):
    with pytest.raises(UploadRejected):
        check(data, name, mime)


def test_tar_is_an_archive():
    head = b"x" * 257 + b"ustar\x0000" + b"\x00" * 100
    assert sniff(head) == "archive"


def test_zip_bomb_is_refused(monkeypatch):
    from services import upload_types

    monkeypatch.setattr(upload_types, "MAX_UNPACKED_BYTES", 1000)
    bomb = _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": "0" * 5000})
    with pytest.raises(UploadRejected, match="unpacks"):
        check(bomb, "x.docx", DOCX_MIME)


def test_file_is_left_at_the_start():
    f = io.BytesIO(DOCX)
    check_upload(f, "a.docx", DOCX_MIME)
    assert f.tell() == 0
    f.seek(5)
    with pytest.raises(UploadRejected):
        check_upload(io.BytesIO(EXE), "a.pdf", "application/pdf")


# ------------------------------------------------------------ widget attachments
def test_widget_refuses_a_renamed_program(isolated_db):
    from models.bots import Bots
    from routes.widget import _validate_uploads

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"t-{uuid.uuid4().hex[:6]}", name="T")
    bad = UploadFile(file=io.BytesIO(EXE), filename="cv.pdf", size=len(EXE),
                     headers={"content-type": "application/pdf"})
    with pytest.raises(HTTPException) as exc:
        _validate_uploads([bad], bot)
    assert exc.value.status_code == 415

    good = UploadFile(file=io.BytesIO(PDF), filename="cv.pdf", size=len(PDF),
                      headers={"content-type": "application/pdf"})
    _validate_uploads([good], bot)
    assert good.file.tell() == 0


# ------------------------------------------------------------ admin uploads
@pytest.fixture
def admin_upload(isolated_db, monkeypatch, tmp_path):
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from routes import admin_bot_files
    from services.auth.dependencies import get_admin

    monkeypatch.setattr(admin_bot_files, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(admin_bot_files, "_kick_embed", lambda *a, **k: None)
    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="sniff@x.example",
                              is_active=True, is_superadmin=True)
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"sn-{uuid.uuid4().hex[:6]}", name="S")
    app = FastAPI()
    app.include_router(admin_bot_files.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: admin
    client = TestClient(app)

    def upload(name, data, mime):
        return client.post(f"/api/admin/bots/{bot.slug}/files", files={"file": (name, data, mime)})

    return upload, tmp_path / "bots" / bot.id / "uploads"


def test_admin_upload_of_a_disguised_zip_is_refused_and_removed(admin_upload):
    upload, uploads = admin_upload
    r = upload("handbook.txt", PLAIN_ZIP, "text/plain")
    assert r.status_code == 415
    assert not uploads.exists() or not any(p.is_file() for p in uploads.rglob("*"))


def test_admin_upload_of_a_real_docx_is_accepted(admin_upload):
    upload, _ = admin_upload
    assert upload("handbook.docx", DOCX, DOCX_MIME).status_code == 202
