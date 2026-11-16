"""G3d: AUTH_OIDC_TRUSTED_EMAIL_CLAIMS trusts named claims only.

Entra ID often omits ``email``; the address comes from ``upn``, which
counted as unverified, so invites never activated. The only fix was
AUTH_OIDC_TRUST_EMAIL_UNCONDITIONALLY, which trusts every claim the IdP
sends. Now a deployment can trust just ``upn``.
"""
from __future__ import annotations

import time

import pytest

from tests.auth.fixtures.jwt_minter import FakeOidcServer, mint

ISSUER = "https://idp.test.local"


def _entra_token(**extra):
    """Shaped like a single-tenant Entra ID id_token without the optional
    email claim."""
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": "emly-admin-api", "sub": "AAAAbbbbCCCC", "iat": now, "exp": now + 600,
              "tid": "11111111-2222-3333-4444-555555555555", "oid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
              "upn": "alice@corp.example", "preferred_username": "alice@corp.example", "name": "Alice",
              "ver": "2.0", **extra}
    return mint(claims)


def _provider(**kw):
    return FakeOidcServer(ISSUER).make_provider(email_claims=("email", "upn"), **kw)


def test_upn_is_unverified_by_default():
    principal = _provider().verify_token(_entra_token())
    assert (principal.email, principal.email_verified) == ("alice@corp.example", False)


def test_a_trusted_claim_counts_as_verified():
    principal = _provider(trusted_email_claims=("upn",)).verify_token(_entra_token())
    assert (principal.email, principal.email_verified) == ("alice@corp.example", True)


def test_other_claims_stay_untrusted():
    p = FakeOidcServer(ISSUER).make_provider(email_claims=("email", "preferred_username", "upn"),
                                             trusted_email_claims=("upn",))
    principal = p.verify_token(_entra_token(upn=None, preferred_username="bob@corp.example"))
    assert (principal.email, principal.email_verified) == ("bob@corp.example", False)


def test_email_claim_still_follows_email_verified():
    p = _provider(trusted_email_claims=("upn",))
    assert p.verify_token(_entra_token(email="a@corp.example", email_verified=False)).email_verified is False
    assert p.verify_token(_entra_token(email="a@corp.example", email_verified=True)).email_verified is True


def test_an_invite_activates_through_a_trusted_upn(isolated_db):
    from models.pending_admins import PendingAdmins
    from services.auth.user_resolver import EmailNotVerified, resolve_or_create_admin

    PendingAdmins.create(email="alice@corp.example", is_superadmin=False)
    with pytest.raises(EmailNotVerified):
        resolve_or_create_admin(_provider().verify_token(_entra_token()))
    admin = resolve_or_create_admin(_provider(trusted_email_claims=("upn",)).verify_token(_entra_token()))
    assert admin.email == "alice@corp.example"


def test_factory_reads_the_env(monkeypatch, caplog):
    from services.auth import factory

    captured = {}
    monkeypatch.setattr(factory, "OidcProvider", lambda **kw: captured.update(kw) or object())
    monkeypatch.setenv("AUTH_OIDC_ISSUER", "https://login.microsoftonline.com/t/v2.0")
    monkeypatch.setenv("AUTH_OIDC_AUDIENCE", "aud")
    monkeypatch.setenv("AUTH_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("AUTH_OIDC_EMAIL_CLAIMS", "email,upn")
    monkeypatch.setenv("AUTH_OIDC_TRUSTED_EMAIL_CLAIMS", "upn, unique_name")
    with caplog.at_level("WARNING", logger="services.auth.factory"):
        factory._build_provider()
    assert captured["trusted_email_claims"] == ("upn", "unique_name")
    assert "unique_name" in caplog.text  # trusted but never read: flagged
