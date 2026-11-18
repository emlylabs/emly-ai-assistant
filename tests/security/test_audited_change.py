"""S21: a security change and its audit row are written together.

Routes changed the database first and audited afterwards, outside any
transaction. With fail-closed auditing on, a failed audit write answered
503 while the grant, role change or delete had already happened.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import admin_admins, admin_bot_dashboard, admin_bots, workspace


def _decorated():
    for module in (admin_admins, admin_bots, admin_bot_dashboard, workspace):
        for name, fn in vars(module).items():
            if callable(fn) and getattr(fn, "__audited_change__", False):
                yield f"{module.__name__}.{name}", inspect.unwrap(fn)


def test_the_permission_routes_are_covered():
    names = {n.rsplit(".", 1)[1] for n, _ in _decorated()}
    for required in ("update_admin", "delete_admin", "bulk_update_admin_memberships", "transfer_bot_ownership",
                     "accept_invite", "grant_membership", "update_membership_role", "revoke_membership",
                     "soft_delete_bot", "bot_put_api_key", "put_workspace_settings"):
        assert required in names


@pytest.mark.parametrize("name,fn", list(_decorated()))
def test_decorated_handlers_never_await(name, fn):
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    assert not any(isinstance(n, ast.Await) for n in ast.walk(tree)), name


def test_failed_audit_rolls_back_the_change(isolated_db, monkeypatch):
    import main
    from models.admin_audit_log import AdminAuditLogs
    from models.admin_users import AdminUsers
    from services.audit import AuditRequestContextMiddleware, AuditUnavailable
    from services.auth.dependencies import get_admin

    monkeypatch.setenv("MODE", "prod")
    root = AdminUsers.insert(id="adm-root", email="root@x.example", is_active=True, is_superadmin=True)
    AdminUsers.insert(id="adm-2", email="two@x.example", is_active=True, is_superadmin=True)
    target = AdminUsers.insert(id="adm-t", email="t@x.example", is_active=True)

    app = FastAPI()
    app.include_router(admin_admins.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: root
    app.add_exception_handler(AuditUnavailable, main.app.exception_handlers[AuditUnavailable])
    app.add_middleware(AuditRequestContextMiddleware)
    client = TestClient(app)

    real_insert = AdminAuditLogs.insert

    def boom(**kw):
        if kw.get("action") == "admin.updated":
            raise RuntimeError("db down")
        return real_insert(**kw)

    monkeypatch.setattr(AdminAuditLogs, "insert", boom)
    r = client.patch(f"/api/admin/admins/{target.id}", json={"is_superadmin": True})
    assert r.status_code == 503
    assert AdminUsers.get_by_id(target.id).is_superadmin is False  # nothing changed

    monkeypatch.setattr(AdminAuditLogs, "insert", real_insert)
    r = client.patch(f"/api/admin/admins/{target.id}", json={"is_superadmin": True})
    assert r.status_code == 200
    assert AdminUsers.get_by_id(target.id).is_superadmin is True


def test_a_refusal_keeps_its_denial_row(isolated_db):
    from models.admin_audit_log import AdminAuditLog
    from models.admin_users import AdminUsers
    from services.auth.dependencies import get_admin

    plain = AdminUsers.insert(id="adm-plain", email="plain@x.example", is_active=True)
    app = FastAPI()
    app.include_router(admin_admins.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: plain
    r = TestClient(app).patch("/api/admin/admins/adm-plain", json={"is_superadmin": True})
    assert r.status_code == 403
    assert AdminAuditLog.select().where(AdminAuditLog.action == "authz.denied").count() == 1
