"""C7 (part 1): audit rows record the caller's IP and user agent even when
the call site passes no Request, and authorization denials are audited."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _rows(action):
    from models.admin_audit_log import AdminAuditLogs

    return AdminAuditLogs.list_filtered(action=action)


@pytest.fixture
def ctx_app(isolated_db):
    from services.audit import AuditRequestContextMiddleware, audit

    app = FastAPI()

    def deep_service_call():
        audit("test.deep", admin_id="adm-1")  # no request anywhere

    @app.post("/do")
    def do():
        deep_service_call()
        return {"ok": True}

    app.add_middleware(AuditRequestContextMiddleware)
    return TestClient(app)


def test_audit_without_request_gets_ip_and_ua_from_context(ctx_app):
    ctx_app.post("/do", headers={"User-Agent": "pytest-agent/1.0"})
    (row,) = _rows("test.deep")
    assert row.ip == "testclient"
    assert row.ua == "pytest-agent/1.0"


def test_context_does_not_leak_between_requests(ctx_app):
    ctx_app.post("/do", headers={"User-Agent": "first"})
    ctx_app.post("/do", headers={"User-Agent": "second"})
    assert sorted(r.ua for r in _rows("test.deep")) == ["first", "second"]


def test_audit_outside_a_request_still_works(isolated_db):
    from services.audit import audit

    audit("test.background")
    (row,) = _rows("test.background")
    assert row.ip is None and row.ua is None


def test_explicit_values_win(ctx_app, isolated_db):
    from services.audit import audit

    audit("test.explicit", ip="10.1.2.3", ua="explicit")
    (row,) = _rows("test.explicit")
    assert (row.ip, row.ua) == ("10.1.2.3", "explicit")


# ------------------------------------------------------- authz denials


@pytest.fixture
def denial_app(isolated_db):
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from routes import admin_bots
    from services.audit import AuditRequestContextMiddleware
    from services.auth.dependencies import get_admin

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"d-{uuid.uuid4().hex[:6]}", name="D")
    stranger = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="s@x.example", is_active=True)
    app = FastAPI()
    app.include_router(admin_bots.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: stranger
    app.add_middleware(AuditRequestContextMiddleware)
    return TestClient(app), bot, stranger


def test_403_is_audited_with_actor_ip_ua(denial_app):
    client, bot, stranger = denial_app
    r = client.patch(f"/api/admin/bots/{bot.slug}", json={"name": "x"}, headers={"User-Agent": "ua-x"})
    assert r.status_code == 403
    rows = _rows("authz.denied")
    assert len(rows) == 1
    row = rows[0]
    assert (row.admin_id, row.bot_id, row.success) == (stranger.id, bot.id, False)
    assert row.payload["code"] in ("not_writer", "not_owner", "not_member")
    assert (row.ip, row.ua) == ("testclient", "ua-x")


def test_superadmin_check_denial_is_audited(isolated_db):
    from models.admin_users import AdminUsers
    from services.authz import require_superadmin

    a = AdminUsers.insert(id="adm-plain", email="p@x.example", is_active=True)
    with pytest.raises(Exception):
        require_superadmin(a)
    (row,) = _rows("authz.denied")
    assert row.payload == {"code": "not_superadmin"}


def test_main_installs_the_context_middleware():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "main.py").read_text()
    assert "app.add_middleware(AuditRequestContextMiddleware)" in src
