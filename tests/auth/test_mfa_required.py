"""G3b: optional MFA check at sign-in (AUTH_OIDC_REQUIRED_ACR / _AMR).

Neither acr nor amr was read, so an admin could sign in through the IdP
with a password alone even where the organisation requires MFA for
admin tools. With the settings on, the callback refuses a sign-in whose
id_token doesn't show the required strength, audits it, and asks the IdP
for the required level up front.
"""
from __future__ import annotations

import time
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from authlib.jose import JsonWebKey, JsonWebToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

ISSUER = "https://idp.test"
AUD = "emly-admin-console"


class FakeIdP:
    """Signs id_tokens with whatever acr/amr the test asks for."""

    def __init__(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption())
        pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
        self.jwk = JsonWebKey.import_key(pub, {"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256"})
        self.claims = {}
        self.nonce = None

    def jwks(self):
        return {"keys": [self.jwk.as_dict()]}

    def token(self, data):
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": AUD, "sub": "sub-mfa", "email": "mfa@corp.example",
                  "email_verified": True, "iat": now, "exp": now + 600, "nonce": self.nonce, **self.claims}
        id_token = JsonWebToken(["RS256"]).encode({"alg": "RS256", "kid": "k1"}, claims,
                                                  self.private_pem).decode()
        return {"access_token": "at", "id_token": id_token, "expires_in": 600}


def _provider(idp, **kw):
    from services.auth.oidc import OidcProvider

    return OidcProvider(
        issuer=ISSUER, audience=AUD, client_id=AUD,
        discovery_override={"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
                            "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/jwks"},
        jwks_provider=idp.jwks, token_exchanger=idp.token, **kw)


@pytest.fixture
def sign_in(isolated_db, monkeypatch):
    """Run login -> callback against FakeIdP. Returns (response, authorize_params)."""
    from models.admin_users import AdminUsers
    from routes import auth as auth_routes
    from services.auth import factory

    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    monkeypatch.setenv("AUTH_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="mfa@corp.example", issuer=ISSUER,
                      subject="sub-mfa", email_verified=True, is_active=True)
    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/api/admin")
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)
    idp = FakeIdP()

    def run(claims, accept="application/json", **provider_kw):
        factory.reset_for_tests()
        factory._provider = _provider(idp, **provider_kw)
        idp.claims = claims
        params = {k: v[0] for k, v in parse_qs(urlparse(
            client.get("/api/admin/auth/login").headers["location"]).query).items()}
        idp.nonce = params["nonce"]
        r = client.get(f"/api/admin/auth/callback?code=c&state={params['state']}",
                       headers={"Accept": accept})
        return r, params

    yield run
    factory.reset_for_tests()


def test_off_by_default(sign_in):
    r, params = sign_in({})
    assert r.status_code == 302 and "acr_values" not in params


@pytest.mark.parametrize("claims", [{}, {"amr": ["pwd"]}, {"amr": "mfa"}])
def test_amr_without_a_required_method_is_refused_and_audited(sign_in, claims):
    from models.admin_audit_log import AdminAuditLogs

    r, _ = sign_in(claims, required_amr=("mfa",))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "mfa_required"
    assert "emly_admin_session" not in r.cookies
    (row,) = AdminAuditLogs.list_filtered(action="auth.login_failed", limit=5)[:1]
    assert row.success is False and row.payload["reason"] == "mfa_required"


def test_amr_with_mfa_signs_in(sign_in):
    r, _ = sign_in({"amr": ["pwd", "mfa"]}, required_amr=("mfa", "hwk"))
    assert r.status_code == 302 and r.cookies.get("emly_admin_session")


def test_acr_is_requested_and_checked(sign_in):
    level = "http://schemas.openid.net/pape/policies/2007/06/multi-factor"
    r, params = sign_in({"acr": "1"}, required_acr=(level,))
    assert params["acr_values"] == level  # the IdP is asked for it up front
    assert r.status_code == 401

    r, _ = sign_in({"acr": level}, required_acr=(level,))
    assert r.status_code == 302


def test_browser_gets_a_readable_page(sign_in):
    r, _ = sign_in({}, accept="text/html", required_amr=("mfa",))
    assert r.status_code == 401
    assert "Multi-factor sign-in required" in r.text


def test_settings_are_read_from_env(monkeypatch):
    from services.auth.factory import required_authentication

    monkeypatch.setenv("AUTH_OIDC_REQUIRED_ACR", "")
    monkeypatch.setenv("AUTH_OIDC_REQUIRED_AMR", "mfa, hwk")
    assert required_authentication() == ((), ("mfa", "hwk"))


def test_built_in_issuer_with_required_mfa_stops_start_up(monkeypatch):
    from services.auth.base import UnknownProviderError
    from services.auth.factory import check_required_authentication

    monkeypatch.setenv("AUTH_OIDC_REQUIRED_AMR", "mfa")
    monkeypatch.delenv("AUTH_OIDC_ISSUER", raising=False)
    with pytest.raises(UnknownProviderError, match="external identity provider"):
        check_required_authentication()
    monkeypatch.setenv("AUTH_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("APP_BASE_URL", "https://emly.corp.example")
    check_required_authentication()  # an external IdP is fine
