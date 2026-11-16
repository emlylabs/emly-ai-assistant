"""G3a: an admin session ends AUTH_SESSION_MAX_AGE_SECONDS after sign-in.

The UI refreshed tokens for as long as a tab was open, and the built-in
issuer handed out a fresh 30-day refresh token on every refresh, so a
session never ended. The sign-in time now rides in the refresh cookie
and the issuer's refresh records, and refreshes past the limit fail.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.secrets import decrypt_refresh_cookie, encrypt_refresh_cookie
from tests.auth.test_mfa_required import ISSUER, FakeIdP, _provider

HOUR = 3600


class RefreshingIdP(FakeIdP):
    def __init__(self):
        super().__init__()
        self.refreshes = 0

    def token(self, data):
        body = super().token(data)
        if data.get("grant_type") == "refresh_token":
            self.refreshes += 1
        body["refresh_token"] = f"rt-{self.refreshes}"
        return body


@pytest.fixture
def app(isolated_db, monkeypatch):
    import uuid

    from models.admin_users import AdminUsers
    from routes import auth as auth_routes, bff_auth
    from services.auth import factory

    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    monkeypatch.setenv("AUTH_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_CSRF_TRUSTED_ORIGINS", "http://testserver")
    AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="mfa@corp.example", issuer=ISSUER,
                      subject="sub-mfa", email_verified=True, is_active=True)
    idp = RefreshingIdP()
    factory.reset_for_tests()
    factory._provider = _provider(idp)
    a = FastAPI()
    a.include_router(auth_routes.router, prefix="/api/admin")
    a.include_router(bff_auth.router)
    client = TestClient(a, base_url="http://testserver", follow_redirects=False)
    yield client, idp
    factory.reset_for_tests()


def _sign_in(client, idp):
    params = {k: v[0] for k, v in parse_qs(urlparse(
        client.get("/api/admin/auth/login").headers["location"]).query).items()}
    idp.nonce = params["nonce"]
    r = client.get(f"/api/admin/auth/callback?code=c&state={params['state']}")
    assert r.status_code == 302, r.text
    return r


def _refresh_cookie(client):
    return decrypt_refresh_cookie(client.cookies.get("emly_admin_refresh"))


def _age_session(client, seconds):
    """Rewrite the refresh cookie as if the sign-in were ``seconds`` ago."""
    cookie = _refresh_cookie(client)
    client.cookies.set("emly_admin_refresh", encrypt_refresh_cookie(cookie.refresh_token,
                                                                    cookie.auth_time - seconds))


def _refresh(client):
    return client.post("/api/auth/refresh", headers={"Origin": "http://testserver"})


# ------------------------------------------------------------- cookie format
def test_cookie_carries_the_sign_in_time():
    c = decrypt_refresh_cookie(encrypt_refresh_cookie("rt", 1_700_000_000))
    assert (c.refresh_token, c.auth_time) == ("rt", 1_700_000_000)
    assert "rt" not in repr(c)


def test_a_stored_secret_cant_be_replayed_as_a_cookie():
    """G6b: the cookie has its own derived key. A bot secret's ciphertext
    (from a backup, say) set as the refresh cookie must not decrypt, or
    the BFF would send the plaintext API key to the IdP."""
    from services.secrets import encrypt

    assert decrypt_refresh_cookie(encrypt("sk-live-api-key")) is None
    assert decrypt_refresh_cookie(encrypt('{"v": 1, "rt": "x", "auth_time": 1}')) is None


def test_cookie_key_differs_from_the_root_key():
    from cryptography.fernet import Fernet, InvalidToken

    from services.secrets import root_keys

    token = encrypt_refresh_cookie("rt", 1)
    with pytest.raises(InvalidToken):
        Fernet(root_keys()[0]).decrypt(token.encode())


def test_cookies_survive_a_root_key_rotation(monkeypatch):
    from cryptography.fernet import Fernet

    from services import secrets

    old = Fernet.generate_key().decode()
    monkeypatch.setenv("BOT_SECRETS_KEY", old)
    secrets.reset_for_tests()
    token = encrypt_refresh_cookie("rt", 5)
    monkeypatch.setenv("BOT_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("BOT_SECRETS_KEY_PREVIOUS", old)
    secrets.reset_for_tests()
    try:
        assert decrypt_refresh_cookie(token).refresh_token == "rt"
    finally:
        monkeypatch.undo()
        secrets.reset_for_tests()


def test_tampered_cookie_is_missing():
    assert decrypt_refresh_cookie("not-a-token") is None


# ---------------------------------------------------------------- BFF refresh
def test_sign_in_starts_the_clock_and_refresh_carries_it(app, monkeypatch):
    client, idp = app
    monkeypatch.setenv("AUTH_SESSION_MAX_AGE_SECONDS", str(12 * HOUR))
    _sign_in(client, idp)
    started = _refresh_cookie(client).auth_time
    assert abs(started - time.time()) < 5

    r = _refresh(client)
    assert r.status_code == 200 and idp.refreshes == 1
    assert _refresh_cookie(client).auth_time == started  # not reset by the refresh


def test_refresh_past_the_maximum_is_refused(app, monkeypatch):
    from models.admin_audit_log import AdminAuditLogs

    client, idp = app
    monkeypatch.setenv("AUTH_SESSION_MAX_AGE_SECONDS", str(12 * HOUR))
    _sign_in(client, idp)
    _age_session(client, 12 * HOUR + 1)

    r = _refresh(client)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "session_expired"
    assert idp.refreshes == 0  # the IdP was never asked
    cleared = [h for h in r.headers.get_list("set-cookie") if h.startswith("emly_admin_refresh=")]
    assert cleared and ("Max-Age=0" in cleared[0] or 'emly_admin_refresh=""' in cleared[0])
    (row,) = AdminAuditLogs.list_filtered(action="auth.session_expired", limit=5)[:1]
    assert row.payload == {"reason": "max_age"} and row.success is False


def test_cookies_never_outlive_the_session(app, monkeypatch):
    client, idp = app
    monkeypatch.setenv("AUTH_SESSION_MAX_AGE_SECONDS", str(12 * HOUR))
    _sign_in(client, idp)
    _age_session(client, 12 * HOUR - 120)  # two minutes left; the ID token lasts ten

    r = _refresh(client)
    assert r.status_code == 200
    ages = [int(h.split("Max-Age=")[1].split(";")[0]) for h in r.headers.get_list("set-cookie")]
    assert ages and all(a <= 120 for a in ages)


def test_no_limit_outside_production_by_default(app, monkeypatch):
    client, idp = app
    monkeypatch.delenv("AUTH_SESSION_MAX_AGE_SECONDS", raising=False)
    monkeypatch.setenv("MODE", "dev")
    _sign_in(client, idp)
    _age_session(client, 30 * 24 * HOUR)
    assert _refresh(client).status_code == 200


@pytest.mark.parametrize("mode,value,expected", [
    ("prod", None, 12 * HOUR), ("dev", None, None), ("prod", "0", None), ("dev", "28800", 28800),
])
def test_defaults(monkeypatch, mode, value, expected):
    from services.auth.session_limits import max_age_seconds

    monkeypatch.setenv("MODE", mode)
    if value is None:
        monkeypatch.delenv("AUTH_SESSION_MAX_AGE_SECONDS", raising=False)
    else:
        monkeypatch.setenv("AUTH_SESSION_MAX_AGE_SECONDS", value)
    assert max_age_seconds() == expected


@pytest.mark.parametrize("value", ["12h", "-5"])
def test_a_malformed_value_stops_start_up(monkeypatch, value):
    from services.auth.session_limits import check_settings

    monkeypatch.setenv("AUTH_SESSION_MAX_AGE_SECONDS", value)
    with pytest.raises(ValueError, match="AUTH_SESSION_MAX_AGE_SECONDS"):
        check_settings()


# ------------------------------------------------------------- built-in issuer
def test_built_in_refresh_chain_keeps_the_sign_in_time_and_ends(monkeypatch):
    """Presented straight to the issuer's token endpoint, a refresh chain
    still can't outlive the session."""
    from services.auth.issuer.flows import CodeExchangeError, consume_refresh_token, issue_refresh_token
    from services.auth.issuer.refresh_store import InMemoryRefreshTokenStore

    monkeypatch.setenv("AUTH_SESSION_MAX_AGE_SECONDS", str(12 * HOUR))
    store = InMemoryRefreshTokenStore()
    signed_in = datetime.now(timezone.utc) - timedelta(hours=11)
    rt = issue_refresh_token(refresh_store=store, admin_id="a", email="a@x.example", email_verified=True,
                             auth_time=signed_in)
    record = consume_refresh_token(refresh_store=store, refresh_token=rt)
    rotated = issue_refresh_token(refresh_store=store, admin_id="a", email="a@x.example",
                                  email_verified=True, auth_time=record.auth_time)
    assert consume_refresh_token(refresh_store=store, refresh_token=rotated).auth_time == signed_in

    old = issue_refresh_token(refresh_store=store, admin_id="a", email="a@x.example", email_verified=True,
                              auth_time=datetime.now(timezone.utc) - timedelta(hours=13))
    with pytest.raises(CodeExchangeError, match="maximum age"):
        consume_refresh_token(refresh_store=store, refresh_token=old)


def test_built_in_sign_in_starts_a_new_chain():
    from services.auth.issuer.flows import consume_refresh_token, issue_refresh_token
    from services.auth.issuer.refresh_store import InMemoryRefreshTokenStore

    store = InMemoryRefreshTokenStore()
    rt = issue_refresh_token(refresh_store=store, admin_id="a", email="a@x.example", email_verified=True)
    record = consume_refresh_token(refresh_store=store, refresh_token=rt)
    assert abs((datetime.now(timezone.utc) - record.auth_time).total_seconds()) < 5
