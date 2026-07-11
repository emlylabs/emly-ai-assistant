"""Regression test: the OIDC callback stores the ``id_token`` (not the
``access_token``) in the session cookie.

The ID token is the OIDC artifact that carries the user's identity claims
(``sub``/``email``/``email_verified``). Access tokens have different
audience semantics — for external IdPs they are often opaque or have an
``aud`` of the resource server rather than the client app. Storing the
access token would break ``verify_token`` on subsequent requests against
any non-trivial IdP.
"""

from __future__ import annotations

import tempfile
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def oidc_app(isolated_db, monkeypatch):
    keys_dir = tempfile.mkdtemp(prefix="cookie-test-keys-")
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    monkeypatch.setenv("AUTH_OIDC_ISSUER", "http://testserver")
    monkeypatch.setenv("AUTH_OIDC_AUDIENCE", "emly-admin-api")
    monkeypatch.setenv("AUTH_OIDC_CLIENT_ID", "emly-admin-console")
    monkeypatch.setenv(
        "AUTH_OIDC_ALLOWED_REDIRECT_URIS",
        "http://testserver/api/admin/auth/callback",
    )
    monkeypatch.setenv("AUTH_LOCAL_ISSUER_ENABLED", "true")
    monkeypatch.setenv("AUTH_LOCAL_KEYS_DIR", keys_dir)
    monkeypatch.setenv("AUTH_LOCAL_KEY_BITS", "2048")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "lax")
    monkeypatch.setenv("AUTH_CSRF_TRUSTED_ORIGINS", "http://testserver")

    from services.auth.factory import reset_for_tests as reset_provider
    from services.auth.issuer.factory import reset_for_tests as reset_issuer

    reset_provider()
    reset_issuer()

    from routes import auth as auth_routes, auth_issuer
    from services.auth import factory as provider_factory
    from services.auth.oidc import OidcProvider

    app = FastAPI()
    app.include_router(auth_issuer.router)
    app.include_router(auth_routes.router, prefix="/api/admin")

    loopback_client = TestClient(app, base_url="http://testserver")
    provider = OidcProvider(
        issuer="http://testserver",
        audience="emly-admin-api",
        client_id="emly-admin-console",
        http_client=loopback_client,
    )
    provider_factory._provider = provider

    yield app

    reset_provider()
    reset_issuer()


@pytest.fixture
def bootstrapped_admin(isolated_db):
    from models.local_credentials import LocalCredentials
    from models.pending_admins import PendingAdmins
    from services.auth.issuer.passwords import hash_password

    email = "alice@example.com"
    password = "test-password-123"
    PendingAdmins.create(email=email, is_superadmin=True, bot_assignments=[])
    LocalCredentials.upsert(
        admin_id=f"bootstrap:{email}",
        password_hash=hash_password(password),
        must_change=True,
    )
    return {"email": email, "password": password}


def test_session_cookie_carries_id_token_not_access_token(oidc_app, bootstrapped_admin):
    """End-to-end: capture the token-endpoint response, then confirm the
    cookie value matches ``id_token`` and not ``access_token``.

    The two tokens differ at the ``nonce`` claim (only id_tokens carry it),
    so we decode the cookie and assert the nonce is present.
    """
    import json
    import base64

    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)
    r = client.get("/api/admin/auth/login")
    authorize_url = r.headers["location"]
    params = {k: v[0] for k, v in parse_qs(urlparse(authorize_url).query).items()}

    form = {
        **params,
        "email": bootstrapped_admin["email"],
        "password": bootstrapped_admin["password"],
    }
    r = client.post("/api/auth/local/authorize", data=form)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

    r = client.get(
        f"/api/admin/auth/callback?code={code}&state={params['state']}"
    )
    assert r.status_code == 302
    cookie_value = r.cookies.get("emly_admin_session")
    assert cookie_value, "session cookie not set"

    # Decode the JWT payload (no signature check — we just want to read claims).
    parts = cookie_value.split(".")
    assert len(parts) == 3, "cookie value is not a JWT"
    pad = "=" * (-len(parts[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + pad))

    # The id_token carries ``nonce``; the access_token does not. Confirm the
    # cookie holds the id_token.
    assert "nonce" in claims, (
        "cookie value is missing nonce claim — it appears to be the access_token, "
        "not the id_token"
    )
    assert claims.get("aud") == "emly-admin-api"
    assert claims.get("email") == bootstrapped_admin["email"]
