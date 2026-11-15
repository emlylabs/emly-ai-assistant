"""G5a: logout and refresh events name the admin.

``auth.logout``, ``auth.refresh``, ``auth.refresh_failed`` and
``auth.refresh_missing`` were written with no actor, so the log couldn't
say whose session ended or failed to renew. The actor comes from a token
that must verify (signature, issuer, audience) but may be expired; a
forged or unknown token gives no actor rather than a wrong one.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.auth.base import ExpiredTokenError, InvalidTokenError, Principal, TokenSet

ISS = "https://idp.example"


class FakeProvider:
    """Tokens: ``live:<sub>``, ``expired:<sub>``, anything else is invalid."""

    def __init__(self):
        self.refresh_ok = True

    def verify_token(self, token, *, allow_expired=False):
        kind, _, sub = (token or "").partition(":")
        if kind == "expired" and not allow_expired:
            raise ExpiredTokenError("expired")
        if kind not in ("live", "expired"):
            raise InvalidTokenError("bad signature")
        return Principal(issuer=ISS, subject=sub, email=f"{sub}@example.com", email_verified=True)

    def logout_url(self, base):
        return None

    def refresh_tokens(self, rt):
        if not self.refresh_ok:
            raise InvalidTokenError("refresh token revoked")
        sub = rt.split(":", 1)[1]
        return TokenSet(access_token="a", id_token=f"live:{sub}", refresh_token=rt,
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))


@pytest.fixture
def env(isolated_db, monkeypatch):
    from models.admin_users import AdminUsers
    from routes import auth as auth_routes, bff_auth
    from services.auth.dependencies import get_auth_provider

    monkeypatch.setenv("AUTH_LOCAL_ISSUER_ENABLED", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    sub = f"sub-{uuid.uuid4().hex[:6]}"
    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email=f"{sub}@example.com", is_active=True,
                              is_superadmin=False, issuer=ISS, subject=sub)
    provider = FakeProvider()
    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/api/admin")
    app.include_router(bff_auth.router)
    app.dependency_overrides[get_auth_provider] = lambda: provider
    return TestClient(app), provider, admin, sub


def _last(action):
    from models.admin_audit_log import AdminAuditLogs

    rows = AdminAuditLogs.list_filtered(action=action, limit=1)
    return rows[0] if rows else None


def _cookies(client, session=None, refresh=None):
    import time as _time

    from services.secrets import encrypt_refresh_cookie

    client.cookies.clear()
    if session:
        client.cookies.set("emly_admin_session", session)
    if refresh:
        client.cookies.set("emly_admin_refresh", encrypt_refresh_cookie(refresh, _time.time()))


@pytest.mark.parametrize("token", ["live", "expired"])
def test_logout_names_the_admin(env, token):
    client, _, admin, sub = env
    _cookies(client, session=f"{token}:{sub}")
    assert client.post("/api/admin/auth/logout").status_code == 200
    assert _last("auth.logout").admin_id == admin.id


@pytest.mark.parametrize("session", [None, "forged:anything", "live:unknown-subject"])
def test_logout_without_a_verifiable_known_token_has_no_actor(env, session):
    client, _, _, _ = env
    _cookies(client, session=session)
    client.post("/api/admin/auth/logout")
    assert _last("auth.logout").admin_id is None


def test_refresh_names_the_admin(env):
    client, _, admin, sub = env
    _cookies(client, session=f"expired:{sub}", refresh=f"rt:{sub}")
    assert client.post("/api/auth/refresh").status_code == 200
    assert _last("auth.refresh").admin_id == admin.id


def test_failed_refresh_names_the_admin(env):
    client, provider, admin, sub = env
    provider.refresh_ok = False
    _cookies(client, session=f"expired:{sub}", refresh=f"rt:{sub}")
    assert client.post("/api/auth/refresh").status_code == 401
    assert _last("auth.refresh_failed").admin_id == admin.id


def test_missing_refresh_token_names_the_admin(env):
    client, _, admin, sub = env
    _cookies(client, session=f"expired:{sub}")
    assert client.post("/api/auth/refresh").status_code == 401
    assert _last("auth.refresh_missing").admin_id == admin.id


def test_oidc_allow_expired_skips_only_the_expiry_check():
    from services.auth.base import InvalidAudienceError
    from services.auth.oidc import OidcProvider

    p = OidcProvider.__new__(OidcProvider)
    p._issuer, p._audience, p._client_id = ISS, "api", "client"
    p._audience_fallback_to_client_id, p._leeway = False, 0
    expired = {"iss": ISS, "aud": "api", "exp": int(time.time()) - 3600}

    with pytest.raises(ExpiredTokenError):
        p._validate_claims(dict(expired))
    p._validate_claims(dict(expired), allow_expired=True)
    with pytest.raises(InvalidAudienceError):
        p._validate_claims({**expired, "aud": "other"}, allow_expired=True)
