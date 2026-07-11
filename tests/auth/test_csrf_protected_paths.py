"""CSRF middleware regression: cookie-authenticated mutating requests on
``/api/admin/`` must be Origin-checked.

The legacy ``/api/v1/`` cookie surface is no longer supported, so the
v1-prefix cases that lived here have been removed.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


@pytest.fixture
def csrf_app(monkeypatch):
    """Tiny app that mounts the CSRF middleware and a stub admin route so we
    can assert the middleware behavior in isolation of the rest of the
    routing stack."""
    monkeypatch.setenv("AUTH_CSRF_TRUSTED_ORIGINS", "http://trusted.example.com")
    monkeypatch.setenv("AUTH_COOKIE_NAME", "emly_admin_session")

    from services.auth.csrf import OriginCheckMiddleware

    app = FastAPI()
    app.add_middleware(OriginCheckMiddleware)

    @app.post("/api/admin/admin-mutate")
    async def admin_mutate():
        return JSONResponse({"ok": True})

    return app


def test_admin_path_blocks_untrusted_origin(csrf_app):
    """Cookie-authed POST on /api/admin/ from an unfriendly origin → 403."""
    client = TestClient(csrf_app)
    r = client.post(
        "/api/admin/admin-mutate",
        cookies={"emly_admin_session": "stub-token"},
        headers={"Origin": "http://attacker.example.com"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "csrf_origin_rejected"


def test_admin_path_allows_trusted_origin(csrf_app):
    client = TestClient(csrf_app)
    r = client.post(
        "/api/admin/admin-mutate",
        cookies={"emly_admin_session": "stub-token"},
        headers={"Origin": "http://trusted.example.com"},
    )
    assert r.status_code == 200


def test_admin_path_blocks_missing_origin(csrf_app):
    """Cookie request with neither Origin nor Referer must 403."""
    client = TestClient(csrf_app)
    r = client.post(
        "/api/admin/admin-mutate",
        cookies={"emly_admin_session": "stub-token"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "csrf_origin_missing"


def test_bearer_only_request_bypasses_csrf(csrf_app):
    """Bearer-only callers (no cookie) carry no ambient credentials, so CSRF
    doesn't apply — the middleware lets them through regardless of Origin."""
    client = TestClient(csrf_app)
    r = client.post(
        "/api/admin/admin-mutate",
        headers={
            "Authorization": "Bearer some-jwt",
            "Origin": "http://attacker.example.com",
        },
    )
    assert r.status_code == 200
