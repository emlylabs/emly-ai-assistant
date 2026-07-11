"""XSS / format-validation regression tests for the embedded issuer's
``/api/auth/local/authorize`` GET endpoint.

The form interpolates query-string values (state, nonce, code_challenge,
client_id, redirect_uri, scope) into hidden ``<input value="...">`` attributes.
Two layers must hold:

  1. Format validation rejects malformed state/nonce/code_challenge so they
     never reach the renderer.
  2. The renderer HTML-escapes every interpolated value, so anything that
     does sneak through (e.g. unrestricted ``client_id`` / ``redirect_uri``)
     can't break out of the attribute context.
"""

from __future__ import annotations

import os
import tempfile
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def issuer_app(isolated_db, monkeypatch):
    keys_dir = tempfile.mkdtemp(prefix="xss-test-keys-")
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

    from services.auth.factory import reset_for_tests as reset_provider
    from services.auth.issuer.factory import reset_for_tests as reset_issuer

    reset_provider()
    reset_issuer()

    from routes import auth_issuer

    app = FastAPI()
    app.include_router(auth_issuer.router)

    yield app

    reset_provider()
    reset_issuer()


def _good_params() -> dict:
    # All valid values matching the configured issuer + a redirect_uri the
    # allowlist accepts. Tests override individual fields.
    return {
        "client_id": "emly-admin-console",
        "redirect_uri": "http://testserver/api/admin/auth/callback",
        "response_type": "code",
        "state": "abc-state-1234567890",
        "nonce": "nonce-abcdef1234",
        "code_challenge": "abcdef-12345_67890-abcdef-1234567890ab",
        "code_challenge_method": "S256",
        "scope": "openid email profile",
    }


def test_authorize_form_renders_for_well_formed_request(issuer_app):
    client = TestClient(issuer_app, base_url="http://testserver")
    r = client.get(f"/api/auth/local/authorize?{urlencode(_good_params())}")
    assert r.status_code == 200
    assert "<form" in r.text


def test_state_with_attacker_payload_is_rejected_pre_render(issuer_app):
    """Format validation refuses a ``state`` containing ``"`` so XSS can never
    even reach the renderer."""
    client = TestClient(issuer_app, base_url="http://testserver")
    params = _good_params()
    params["state"] = '"><script>alert(1)</script>'
    r = client.get(f"/api/auth/local/authorize?{urlencode(params)}")
    assert r.status_code == 400
    assert "state" in r.text.lower()


def test_code_challenge_with_attacker_payload_is_rejected_pre_render(issuer_app):
    client = TestClient(issuer_app, base_url="http://testserver")
    params = _good_params()
    params["code_challenge"] = '"><img src=x onerror=alert(1)>'
    r = client.get(f"/api/auth/local/authorize?{urlencode(params)}")
    assert r.status_code == 400


def test_nonce_with_attacker_payload_is_rejected_pre_render(issuer_app):
    client = TestClient(issuer_app, base_url="http://testserver")
    params = _good_params()
    params["nonce"] = '"><svg/onload=alert(1)>'
    r = client.get(f"/api/auth/local/authorize?{urlencode(params)}")
    assert r.status_code == 400


def test_redirect_uri_attacker_payload_is_html_escaped_when_rendered(issuer_app):
    """``redirect_uri`` is allowlist-checked against ``AUTH_OIDC_ALLOWED_REDIRECT_URIS``,
    so a literal attacker URL is 4xx'd before rendering. But to lock in the
    second layer, force the value through the renderer by adding the attacker
    URL to the allowlist for this test, then check that quotes/angle-brackets
    are escaped on output."""
    os.environ["AUTH_OIDC_ALLOWED_REDIRECT_URIS"] = (
        'http://testserver/api/admin/auth/callback,'
        'http://attacker.example.com/"><script>alert(1)</script>'
    )
    client = TestClient(issuer_app, base_url="http://testserver")
    params = _good_params()
    params["redirect_uri"] = 'http://attacker.example.com/"><script>alert(1)</script>'
    r = client.get(f"/api/auth/local/authorize?{urlencode(params)}")
    # If reached the renderer, quotes/angle-brackets must be escaped to entities.
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text
    assert "&quot;" in r.text


def test_scope_value_html_escaped_in_form(issuer_app):
    """Attacker-controlled ``scope`` reaches the form (no allowlist on it).
    Test that the renderer escapes it."""
    client = TestClient(issuer_app, base_url="http://testserver")
    params = _good_params()
    params["scope"] = 'openid"><script>alert(1)</script>'
    r = client.get(f"/api/auth/local/authorize?{urlencode(params)}")
    assert r.status_code == 200
    # Raw payload must NOT appear; the escaped form must appear.
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text
