"""C6: Secure by default in production, SameSite=lax by design, and logout
revoking the built-in issuer's refresh token directly."""

from __future__ import annotations

import tempfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _req(scheme: str):
    return SimpleNamespace(url=SimpleNamespace(scheme=scheme))


@pytest.mark.parametrize(
    "explicit, mode, base, scheme, expected",
    [
        (None, "prod", "http://internal:8080", "http", True),   # prod behind an untrusted TLS proxy
        (None, "dev", "https://emly.example.com", "http", True),  # https public URL
        (None, "dev", "http://localhost:8080", "http", False),   # local dev
        (None, "dev", "", "https", True),                        # direct https
        ("false", "prod", "https://emly.example.com", "https", False),  # explicit wins
        ("true", "dev", "http://localhost:8080", "http", True),
    ],
)
def test_cookie_secure(monkeypatch, explicit, mode, base, scheme, expected):
    from services.auth.cookie_policy import cookie_secure

    monkeypatch.setenv("MODE", mode)
    monkeypatch.setenv("APP_BASE_URL", base)
    if explicit is None:
        monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    else:
        monkeypatch.setenv("AUTH_COOKIE_SECURE", explicit)
    assert cookie_secure(_req(scheme)) is expected


def test_samesite_defaults_to_lax(monkeypatch):
    from services.auth.cookie_policy import cookie_samesite

    monkeypatch.delenv("AUTH_COOKIE_SAMESITE", raising=False)
    assert cookie_samesite() == "lax"
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "bogus")
    assert cookie_samesite() == "lax"
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "strict")
    assert cookie_samesite() == "strict"


def test_both_routers_share_the_policy(monkeypatch):
    from routes import auth, bff_auth

    monkeypatch.setenv("MODE", "prod")
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    assert auth._cookie_secure(_req("http")) is True
    assert bff_auth._cookie_secure(_req("http")) is True


def test_session_cookies_are_secure_httponly_lax_in_prod(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from fastapi.responses import JSONResponse

    from routes import auth

    monkeypatch.setenv("MODE", "prod")
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    resp = JSONResponse({})
    auth._set_session_cookies(
        resp, _req("http"), id_token="idt", refresh_token="rt",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    cookies = resp.headers.getlist("set-cookie")
    assert len(cookies) == 3
    for c in cookies:
        assert "Secure" in c and "SameSite=lax" in c
    session = next(c for c in cookies if c.startswith(auth._cookie_name() + "="))
    assert "HttpOnly" in session


# ------------------------------------------------------------------ logout


@pytest.fixture
def logout_client(isolated_db, monkeypatch):
    monkeypatch.setenv("AUTH_LOCAL_ISSUER_ENABLED", "true")
    monkeypatch.setenv("AUTH_LOCAL_KEYS_DIR", tempfile.mkdtemp(prefix="c6-keys-"))
    from services.auth.issuer.factory import reset_for_tests

    reset_for_tests()
    from routes import auth
    from services.auth.dependencies import get_auth_provider

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/admin")
    app.dependency_overrides[get_auth_provider] = lambda: SimpleNamespace(logout_url=lambda base: None)
    yield TestClient(app)
    reset_for_tests()


def test_logout_revokes_refresh_token_without_http_loopback(logout_client, monkeypatch):
    import httpx

    from routes import auth
    from services.auth.issuer.factory import get_refresh_store
    from services.auth.issuer.flows import issue_refresh_token
    from services.secrets import encrypt_session_cookie

    def _no_http(*a, **kw):
        raise AssertionError("logout must not call back into the app over HTTP")

    monkeypatch.setattr(httpx, "post", _no_http)
    rt = issue_refresh_token(refresh_store=get_refresh_store(), admin_id="a@x.example",
                             email="a@x.example", email_verified=True)
    logout_client.cookies.set(auth._refresh_cookie_name(), encrypt_session_cookie(rt))
    r = logout_client.post("/api/admin/auth/logout")
    assert r.status_code == 200
    assert get_refresh_store().pop(rt) is None  # revoked
