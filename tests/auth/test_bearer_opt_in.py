"""Q-G3b: admin APIs accept a Bearer header only when AUTH_ALLOW_BEARER is on.

The admin UI uses the session cookie. Accepting the same ID token as a
Bearer header meant a token copied out of a browser (or a log) could be
replayed from anywhere until it expired, with no CSRF check (Bearer-only
requests skip it).
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


_NOW = datetime(2026, 1, 1)


class _Principal:
    issuer = "https://idp.test/"
    subject = "sub-b"
    email = "b@example.com"


class _Provider:
    def verify_token(self, token, *, allow_expired=False):
        if token != "good":
            from services.auth.base import InvalidTokenError

            raise InvalidTokenError("bad")
        return _Principal()


@pytest.fixture
def client(monkeypatch):
    from models.admin_users import AdminUserModel
    from services.auth import dependencies
    from services.auth.dependencies import get_admin, get_auth_provider

    monkeypatch.setattr(dependencies, "resolve_or_create_admin",
                        lambda p, record_login=False: AdminUserModel(id="adm-b", email=p.email, is_active=True, created_at=_NOW, updated_at=_NOW))
    app = FastAPI()

    @app.get("/me")
    def me(admin=Depends(get_admin)):
        return {"id": admin.id}

    app.dependency_overrides[get_auth_provider] = lambda: _Provider()
    return TestClient(app)


def test_bearer_is_refused_by_default(client, monkeypatch):
    monkeypatch.delenv("AUTH_ALLOW_BEARER", raising=False)
    r = client.get("/me", headers={"Authorization": "Bearer good"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "bearer_disabled"


def test_cookie_still_works_by_default(client, monkeypatch):
    monkeypatch.delenv("AUTH_ALLOW_BEARER", raising=False)
    client.cookies.set("emly_admin_session", "good")
    assert client.get("/me").json() == {"id": "adm-b"}


def test_bearer_works_when_allowed(client, monkeypatch):
    monkeypatch.setenv("AUTH_ALLOW_BEARER", "true")
    assert client.get("/me", headers={"Authorization": "Bearer good"}).json() == {"id": "adm-b"}
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).json()["detail"]["code"] == "invalid_token"


def test_audit_actor_ignores_a_disabled_bearer(monkeypatch):
    from starlette.requests import Request

    from services.auth.dependencies import audit_actor_id

    monkeypatch.delenv("AUTH_ALLOW_BEARER", raising=False)
    req = Request({"type": "http", "headers": [(b"authorization", b"Bearer good")], "method": "GET",
                   "path": "/", "query_string": b""})
    assert audit_actor_id(req, _Provider()) is None
