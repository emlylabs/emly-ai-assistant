"""End-to-end test: full OIDC dance against the embedded issuer.

Exercises:
  GET  /api/admin/auth/login
    →  POST /api/auth/local/authorize  (form-post creds)
    →  GET  /api/admin/auth/callback?code=...&state=...
    →  GET  /api/admin/auth/me  (cookie auth)
    →  POST /api/admin/auth/logout

Proves the full authorize-code-with-PKCE flow works against our own
embedded issuer through the same code path the verifier would use against
any external IdP. This is the load-bearing integration test for Phase 4.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def oidc_app(isolated_db, monkeypatch):
    """Builds a fresh FastAPI app with the auth + auth_issuer routers, the
    embedded issuer enabled, and an ``OidcProvider`` pre-constructed with a
    transport that loops back through the same app — so discovery + JWKS +
    token-exchange happen in-process without real network.
    """
    keys_dir = tempfile.mkdtemp(prefix="oidc-e2e-keys-")

    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    monkeypatch.setenv("AUTH_OIDC_ISSUER", "http://testserver")
    monkeypatch.setenv("AUTH_OIDC_AUDIENCE", "emly-admin-api")
    monkeypatch.setenv("AUTH_OIDC_CLIENT_ID", "emly-admin-console")
    monkeypatch.setenv("AUTH_OIDC_ALLOWED_REDIRECT_URIS", "http://testserver/api/admin/auth/callback")
    monkeypatch.setenv("AUTH_LOCAL_ISSUER_ENABLED", "true")
    monkeypatch.setenv("AUTH_LOCAL_KEYS_DIR", keys_dir)
    monkeypatch.setenv("AUTH_LOCAL_KEY_BITS", "2048")  # smaller for test speed
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")  # TestClient is http
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
    app.include_router(auth_issuer.router, prefix="", tags=["issuer"])
    app.include_router(auth_routes.router, prefix="/api/admin", tags=["auth"])

    # Build OidcProvider with a TestClient as the http_client — TestClient
    # is an httpx.Client subclass that drives the in-process ASGI app
    # synchronously, so OidcProvider's discovery/JWKS/token-exchange calls
    # all loop back through the same app without real network.
    loopback_client = TestClient(app, base_url="http://testserver")
    provider = OidcProvider(
        issuer="http://testserver",
        audience="emly-admin-api",
        client_id="emly-admin-console",
        http_client=loopback_client,
    )
    # Inject as the singleton so `routes/auth.py::get_auth_provider` returns
    # this exact instance (with its ASGI-loopback transport already wired).
    provider_factory._provider = provider

    yield app

    reset_provider()
    reset_issuer()


@pytest.fixture
def bootstrapped_admin(isolated_db):
    """Stage a pending superadmin + bootstrap credential, mirroring main.py's
    _bootstrap_pending_superadmin."""
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


def _follow_authorize_url(client: TestClient, authorize_url: str) -> tuple[dict, str, str, str]:
    """Step 1 of the dance: turn the IdP's authorize URL into the form params
    we'll need for POST /api/auth/local/authorize."""
    parsed = urlparse(authorize_url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    return (
        params,
        params["state"],
        params["code_challenge"],
        params["code_challenge_method"],
    )


def test_full_oidc_dance_against_embedded_issuer(oidc_app, bootstrapped_admin):
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)

    # 1) GET /api/admin/auth/login → 302 to embedded issuer's authorize endpoint.
    r = client.get("/api/admin/auth/login")
    assert r.status_code == 302
    authorize_url = r.headers["location"]
    assert authorize_url.startswith("http://testserver/api/auth/local/authorize")
    params, state, code_challenge, _method = _follow_authorize_url(client, authorize_url)

    # 2) POST credentials to the embedded issuer's authorize endpoint.
    #    On success it 302s to redirect_uri?code=...&state=...
    form = {
        **params,
        "email": bootstrapped_admin["email"],
        "password": bootstrapped_admin["password"],
    }
    r = client.post("/api/auth/local/authorize", data=form)
    assert r.status_code == 302, f"authorize_post body: {r.text}"
    callback_url = r.headers["location"]
    assert callback_url.startswith("http://testserver/api/admin/auth/callback")
    cb_params = {k: v[0] for k, v in parse_qs(urlparse(callback_url).query).items()}
    assert cb_params["state"] == state
    code = cb_params["code"]

    # 3) Hit the callback. The route exchanges the code, verifies the id_token,
    #    activates the pending admin (creating the row), and sets the cookie.
    r = client.get(f"/api/admin/auth/callback?code={code}&state={state}")
    assert r.status_code == 302, f"callback body: {r.text}"
    assert r.headers["location"].startswith("http://testserver")
    cookies = r.cookies
    cookie_name = os.environ["AUTH_COOKIE_NAME"] if "AUTH_COOKIE_NAME" in os.environ else "emly_admin_session"
    assert cookie_name in cookies, f"expected cookie {cookie_name} to be set, got {dict(cookies)}"

    # 4) The admin row was created (pending consumed). Verify by /me.
    client.cookies.set(cookie_name, cookies[cookie_name])
    r = client.get("/api/admin/auth/me")
    assert r.status_code == 200, f"/me body: {r.text}"
    body = r.json()
    assert body["email"] == bootstrapped_admin["email"]
    assert body["is_superadmin"] is True
    assert body["is_active"] is True

    # 5) Logout clears the cookie.
    r = client.post("/api/admin/auth/logout")
    assert r.status_code == 200
    # Cookie cleared on the response.
    assert any("Max-Age=0" in v or "max-age=0" in v.lower() for v in r.headers.get_list("set-cookie")) or any(
        "expires=" in v.lower() for v in r.headers.get_list("set-cookie")
    )


def test_callback_with_bad_state_rejected(oidc_app, bootstrapped_admin):
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)
    r = client.get("/api/admin/auth/callback?code=anything&state=did-not-issue-this")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "callback_state_invalid_or_expired"


def test_callback_missing_state_rejected(oidc_app, bootstrapped_admin):
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)
    r = client.get("/api/admin/auth/callback?code=anything")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "missing_state_or_code"


def test_callback_propagates_idp_error(oidc_app, bootstrapped_admin):
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)
    r = client.get("/api/admin/auth/callback?error=access_denied&error_description=user+cancelled")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "idp_error"


def test_me_without_cookie_returns_401(oidc_app, bootstrapped_admin):
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)
    r = client.get("/api/admin/auth/me")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "missing_token"


def test_me_with_invalid_cookie_returns_401(oidc_app, bootstrapped_admin):
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)
    client.cookies.set("emly_admin_session", "not-a-real-jwt")
    r = client.get("/api/admin/auth/me")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_token"


def test_login_with_unknown_email_shows_form_again(oidc_app, bootstrapped_admin):
    """Authorize endpoint re-renders the form on bad credentials, doesn't 4xx."""
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)

    r = client.get("/api/admin/auth/login")
    params, _state, _challenge, _method = _follow_authorize_url(client, r.headers["location"])

    form = {
        **params,
        "email": "unknown@example.com",
        "password": "anything",
    }
    r = client.post("/api/auth/local/authorize", data=form)
    assert r.status_code == 200
    assert "Invalid email or password" in r.text


def test_login_return_to_propagates_to_callback_redirect(oidc_app, bootstrapped_admin):
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)

    r = client.get("/api/admin/auth/login?return_to=/bots/abc")
    params, state, _challenge, _method = _follow_authorize_url(client, r.headers["location"])

    form = {
        **params,
        "email": bootstrapped_admin["email"],
        "password": bootstrapped_admin["password"],
    }
    r = client.post("/api/auth/local/authorize", data=form)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

    r = client.get(f"/api/admin/auth/callback?code={code}&state={state}")
    assert r.status_code == 302
    assert r.headers["location"] == "http://testserver/bots/abc"


def test_login_rejects_offsite_return_to_open_redirect(oidc_app, bootstrapped_admin):
    """`return_to` must be a same-origin path, not an absolute URL."""
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)

    r = client.get("/api/admin/auth/login?return_to=https://attacker.example.com/cookies")
    params, state, _, _ = _follow_authorize_url(client, r.headers["location"])

    form = {
        **params,
        "email": bootstrapped_admin["email"],
        "password": bootstrapped_admin["password"],
    }
    r = client.post("/api/auth/local/authorize", data=form)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

    r = client.get(f"/api/admin/auth/callback?code={code}&state={state}")
    assert r.status_code == 302
    # Falls back to the post-login default (the app root), not the attacker URL.
    assert r.headers["location"] == "http://testserver/"


def test_subsequent_login_after_activation_uses_real_admin_id(oidc_app, bootstrapped_admin):
    """After the first login activates the pending admin, the bootstrap-credential
    sentinel is migrated onto admin.id; the next login authenticates against
    that id."""
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)

    def _full_dance() -> dict:
        r = client.get("/api/admin/auth/login")
        params, state, _, _ = _follow_authorize_url(client, r.headers["location"])
        form = {
            **params,
            "email": bootstrapped_admin["email"],
            "password": bootstrapped_admin["password"],
        }
        r = client.post("/api/auth/local/authorize", data=form)
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        r = client.get(f"/api/admin/auth/callback?code={code}&state={state}")
        assert r.status_code == 302, r.text
        # Pull the cookie off the response and re-attach for /me.
        cookie_jar = dict(r.cookies)
        client.cookies.set("emly_admin_session", cookie_jar["emly_admin_session"])
        r = client.get("/api/admin/auth/me")
        assert r.status_code == 200, r.text
        return r.json()

    me1 = _full_dance()
    # Verify the bootstrap credential was migrated to admin.id.
    from models.local_credentials import LocalCredentials
    assert LocalCredentials.get(f"bootstrap:{bootstrapped_admin['email']}") is None
    assert LocalCredentials.get(me1["id"]) is not None

    # Second login uses the migrated credential.
    me2 = _full_dance()
    assert me2["id"] == me1["id"]
    assert me2["is_superadmin"] is True
