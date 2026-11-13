"""B4: rate limits on widget and local sign-in endpoints, and the OTP
send path refusing cleanly when email is disabled."""

from __future__ import annotations

import json
import tempfile
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def small_limits(monkeypatch):
    """Shrink every named limit to 3/minute so bursts are quick."""
    from services.auth import ratelimit

    for name in list(ratelimit._LIMITS):
        monkeypatch.setitem(ratelimit._LIMITS, name, "3/minute")
    return 3


@pytest.fixture
def widget_env(isolated_db, small_limits):
    from models.bots import Bots
    from routes import chat, widget
    from services.auth.widget_hmac import issue
    from utils.dependencies import get_agent_service

    class _Agent:
        def is_available(self):
            return True

        def process_message(self, **kw):
            return ("ok", [])

    def make_bot():
        return Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"r-{uuid.uuid4().hex[:6]}", name="R")

    app = FastAPI()
    app.include_router(widget.router)
    app.include_router(chat.router)
    app.dependency_overrides[get_agent_service] = lambda: _Agent()

    def auth(bot, user="alice"):
        token = issue(bot_id=bot.id, user_id=user, session_id="s", key_version=bot.widget_key_version)
        return {"Authorization": f"Bearer {token}", "X-Emly-UserID": user}

    return {"client": TestClient(app), "make_bot": make_bot, "auth": auth, "limit": small_limits}


def _burst(send, limit):
    statuses = [send().status_code for _ in range(limit + 1)]
    return statuses


def _assert_limited(resp):
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"]["code"] == "rate_limited"
    assert int(resp.headers["Retry-After"]) >= 1


def test_widget_init_burst_429(widget_env):
    c, bot, n = widget_env["client"], widget_env["make_bot"](), widget_env["limit"]
    send = lambda: c.post(f"/widget/{bot.id}/init", json={})
    statuses = _burst(send, n)
    assert statuses[:n] == [200] * n
    _assert_limited(send())


def test_widget_init_limit_is_per_bot(widget_env):
    c, n = widget_env["client"], widget_env["limit"]
    a, b = widget_env["make_bot"](), widget_env["make_bot"]()
    for _ in range(n + 1):
        c.post(f"/widget/{a.id}/init", json={})
    assert c.post(f"/widget/{b.id}/init", json={}).status_code == 200


def test_widget_chat_burst_429(widget_env):
    c, bot, n = widget_env["client"], widget_env["make_bot"](), widget_env["limit"]
    body = {"user_id": "alice", "session_id": "sess-1", "timestamp": 1,
            "messages": [{"role": "user", "content": "hi"}], "stream": False}
    send = lambda: c.post(f"/widget/{bot.id}/chat", headers=widget_env["auth"](bot), json=body)
    assert _burst(send, n)[:n] == [200] * n
    _assert_limited(send())


def test_widget_chat_limit_applies_before_auth(widget_env):
    """Tokenless floods are throttled too, not just authenticated ones."""
    c, bot, n = widget_env["client"], widget_env["make_bot"](), widget_env["limit"]
    send = lambda: c.post(f"/widget/{bot.id}/chat", json={})
    assert _burst(send, n)[:n] == [401] * n
    _assert_limited(send())


def test_widget_action_burst_429(widget_env):
    c, bot, n = widget_env["client"], widget_env["make_bot"](), widget_env["limit"]
    payload = json.dumps({"session_id": "s1", "action_name": "click", "action_value": "x"})
    send = lambda: c.post(f"/widget/{bot.id}/action", headers=widget_env["auth"](bot), data={"payload": payload})
    assert _burst(send, n)[:n] == [200] * n
    _assert_limited(send())


def test_widget_upload_has_its_own_limit(widget_env, monkeypatch):
    from routes import widget as widget_routes
    from services.auth import ratelimit

    monkeypatch.setitem(ratelimit._LIMITS, "widget_action", "100/minute")
    monkeypatch.setitem(ratelimit._LIMITS, "widget_upload", "2/minute")

    async def _no_uploads(files, user_id, bot_id):
        return []

    monkeypatch.setattr(widget_routes, "handle_file_uploads", _no_uploads)
    c, bot = widget_env["client"], widget_env["make_bot"]()
    payload = json.dumps({"session_id": "s1", "action_name": "upload", "action_value": "x"})

    def send(with_file=True):
        files = {"files": ("a.txt", b"hello", "text/plain")} if with_file else None
        return c.post(f"/widget/{bot.id}/action", headers=widget_env["auth"](bot),
                      data={"payload": payload}, files=files)

    assert send().status_code == 200
    assert send().status_code == 200
    _assert_limited(send())
    # Plain actions are still allowed.
    assert send(with_file=False).status_code == 200


def _otp_payload(email, kind="auth_authentication", **extra):
    return json.dumps({"user_id": email, "form_title": kind, "visitor_id": "alice", **extra})


def test_otp_send_limited_per_recipient(widget_env, monkeypatch):
    from routes import widget as widget_routes

    monkeypatch.setattr(widget_routes, "create_otp", lambda payload, bot_id: None)
    monkeypatch.setattr(widget_routes, "email_enabled", lambda: True)
    c, bot, n = widget_env["client"], widget_env["make_bot"](), widget_env["limit"]
    send = lambda email: c.post(f"/widget/{bot.id}/action", headers=widget_env["auth"](bot),
                                data={"payload": _otp_payload(email)})
    from services.auth import ratelimit

    monkeypatch.setitem(ratelimit._LIMITS, "widget_action", "100/minute")
    monkeypatch.setitem(ratelimit._LIMITS, "otp_send_ip", "100/minute")
    for _ in range(n):
        assert send("victim@example.com").status_code == 200
    _assert_limited(send("Victim@Example.com"))
    assert send("someone-else@example.com").status_code == 200


def test_otp_verify_burst_429(widget_env, monkeypatch):
    from routes import widget as widget_routes
    from services.auth import ratelimit

    monkeypatch.setitem(ratelimit._LIMITS, "widget_action", "100/minute")
    monkeypatch.setattr(widget_routes, "authorize_otp", lambda payload, bot_id: {"code": 401, "message": "Invalid OTP"})
    c, bot, n = widget_env["client"], widget_env["make_bot"](), widget_env["limit"]
    send = lambda: c.post(f"/widget/{bot.id}/action", headers=widget_env["auth"](bot),
                          data={"payload": _otp_payload("a@example.com", "auth_verification", otp="000000")})
    assert _burst(send, n)[:n] == [401] * n
    _assert_limited(send())


def test_otp_send_when_email_disabled_is_503_and_issues_nothing(widget_env, monkeypatch):
    from models.otp_auth import OtpAuthorization
    from routes import widget as widget_routes

    monkeypatch.setattr(widget_routes, "email_enabled", lambda: False)
    c, bot = widget_env["client"], widget_env["make_bot"]()
    r = c.post(f"/widget/{bot.id}/action", headers=widget_env["auth"](bot),
               data={"payload": _otp_payload("a@example.com")})
    assert r.status_code == 503
    assert r.json()["status"] == "failed"
    assert "isn't available" in r.json()["message"]
    assert OtpAuthorization.get_otp(bot.id, "a@example.com") is None


# ---------------------------------------------------------- local issuer


@pytest.fixture
def issuer_client(isolated_db, monkeypatch, small_limits):
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    monkeypatch.setenv("AUTH_OIDC_ISSUER", "http://testserver")
    monkeypatch.setenv("AUTH_OIDC_AUDIENCE", "emly-admin-api")
    monkeypatch.setenv("AUTH_OIDC_CLIENT_ID", "emly-admin-console")
    monkeypatch.setenv("AUTH_OIDC_ALLOWED_REDIRECT_URIS", "http://testserver/api/admin/auth/callback")
    monkeypatch.setenv("AUTH_LOCAL_ISSUER_ENABLED", "true")
    monkeypatch.setenv("AUTH_LOCAL_KEYS_DIR", tempfile.mkdtemp(prefix="rl-keys-"))
    monkeypatch.setenv("AUTH_LOCAL_KEY_BITS", "2048")
    from services.auth.factory import reset_for_tests as reset_provider
    from services.auth.issuer.factory import reset_for_tests as reset_issuer

    reset_provider()
    reset_issuer()
    from routes import auth_issuer

    app = FastAPI()
    app.include_router(auth_issuer.router)
    yield TestClient(app)
    reset_provider()
    reset_issuer()


def test_local_authorize_post_burst_429(issuer_client, small_limits):
    send = lambda: issuer_client.post("/api/auth/local/authorize", data={"email": "a@b.c", "password": "x"})
    statuses = _burst(send, small_limits)
    assert 429 not in statuses[:small_limits]
    _assert_limited(send())


def test_local_authorize_get_and_post_share_limit(issuer_client, small_limits):
    for _ in range(small_limits):
        issuer_client.get("/api/auth/local/authorize")
    _assert_limited(issuer_client.post("/api/auth/local/authorize", data={}))


def test_local_token_burst_429(issuer_client, small_limits):
    send = lambda: issuer_client.post("/api/auth/local/token", data={"grant_type": "authorization_code"})
    statuses = _burst(send, small_limits)
    assert 429 not in statuses[:small_limits]
    _assert_limited(send())
