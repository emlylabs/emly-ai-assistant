"""C1: IdP compatibility — optional offline_access (Google) and email
claim fallback (Entra ID), plus graceful BFF refresh without a refresh
token."""

from __future__ import annotations

import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest

from services.auth.base import MissingClaimError
from services.auth.oidc import OAuthState, generate_pkce_verifier
from tests.auth.fixtures.jwt_minter import FakeOidcServer, mint

ISSUER = "https://idp.test.local"


def _claims(**overrides):
    now = int(time.time())
    base = {"iss": ISSUER, "aud": "emly-admin-api", "sub": "u-1", "iat": now, "exp": now + 600}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def _provider(**kwargs):
    return FakeOidcServer(ISSUER).make_provider(**kwargs)


def _scopes(provider) -> list[str]:
    state = OAuthState(state="S", code_verifier=generate_pkce_verifier(), nonce="N",
                       return_to=None, created_at=datetime.now())
    url = provider.authorize_url(state, "https://app.test/callback")
    return parse_qs(urlparse(url).query)["scope"][0].split()


# ------------------------------------------------------------ offline_access


def test_offline_access_requested_by_default():
    assert "offline_access" in _scopes(_provider())


def test_offline_access_can_be_turned_off():
    scopes = _scopes(_provider(request_offline_access=False))
    assert "offline_access" not in scopes
    assert scopes[:1] == ["openid"]


@pytest.mark.parametrize("value, expected", [(None, True), ("true", True), ("false", False), ("0", False)])
def test_offline_access_env(monkeypatch, value, expected):
    from services.auth.factory import offline_access_requested

    if value is None:
        monkeypatch.delenv("AUTH_OIDC_REQUEST_OFFLINE_ACCESS", raising=False)
    else:
        monkeypatch.setenv("AUTH_OIDC_REQUEST_OFFLINE_ACCESS", value)
    assert offline_access_requested() is expected


# ---------------------------------------------------------------- email claim


def test_default_still_requires_email_claim():
    with pytest.raises(MissingClaimError):
        _provider().verify_token(mint(_claims(upn="alice@corp.example")))


def test_falls_back_to_upn_and_marks_unverified():
    p = _provider(email_claims=("email", "preferred_username", "upn"))
    principal = p.verify_token(mint(_claims(upn="alice@corp.example", email_verified=True)))
    assert principal.email == "alice@corp.example"
    # email_verified speaks for the `email` claim only.
    assert principal.email_verified is False


def test_fallback_skips_values_that_are_not_addresses():
    p = _provider(email_claims=("email", "preferred_username", "upn"))
    principal = p.verify_token(mint(_claims(preferred_username="alice", upn="alice@corp.example")))
    assert principal.email == "alice@corp.example"


def test_email_claim_preferred_when_present():
    p = _provider(email_claims=("email", "upn"))
    principal = p.verify_token(mint(_claims(email="a@x.example", email_verified=True, upn="other@x.example")))
    assert (principal.email, principal.email_verified) == ("a@x.example", True)


def test_trusted_idp_fallback_counts_as_verified():
    p = _provider(email_claims=("email", "upn"), trust_email_unconditionally=True)
    assert p.verify_token(mint(_claims(upn="a@corp.example"))).email_verified is True


def test_no_usable_claim_is_rejected():
    p = _provider(email_claims=("email", "upn"))
    with pytest.raises(MissingClaimError, match="email, upn"):
        p.verify_token(mint(_claims(upn="not-an-address")))


def test_factory_reads_email_claims_env(monkeypatch):
    from services.auth import factory

    captured = {}
    monkeypatch.setattr(factory, "OidcProvider", lambda **kw: captured.update(kw) or object())
    monkeypatch.setenv("AUTH_OIDC_ISSUER", "https://login.microsoftonline.com/t/v2.0")
    monkeypatch.setenv("AUTH_OIDC_AUDIENCE", "aud")
    monkeypatch.setenv("AUTH_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("AUTH_OIDC_EMAIL_CLAIMS", "email, preferred_username ,upn")
    monkeypatch.setenv("AUTH_OIDC_REQUEST_OFFLINE_ACCESS", "false")
    factory._build_provider()
    assert captured["email_claims"] == ("email", "preferred_username", "upn")
    assert captured["request_offline_access"] is False


# ------------------------------------------------- BFF refresh without token


@pytest.fixture
def bff_client(isolated_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import bff_auth
    from services.auth.dependencies import get_auth_provider

    app = FastAPI()
    app.include_router(bff_auth.router)
    app.dependency_overrides[get_auth_provider] = lambda: object()
    return TestClient(app)


@pytest.mark.parametrize("offline, audited", [("false", False), ("true", True)])
def test_refresh_without_token_is_401_and_only_audited_when_unexpected(bff_client, monkeypatch, offline, audited):
    from models.admin_audit_log import AdminAuditLogs

    monkeypatch.setenv("AUTH_OIDC_REQUEST_OFFLINE_ACCESS", offline)
    r = bff_client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "missing_refresh_token"
    assert bool(AdminAuditLogs.list_filtered(action="auth.refresh_missing")) is audited
