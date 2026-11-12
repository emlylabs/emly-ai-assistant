"""B2: widget routes require a verified widget token bound to bot + user."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeAgent:
    def __init__(self):
        self.calls = []

    def is_available(self):
        return True

    def process_message(self, **kw):
        self.calls.append(kw)
        return ("ok", [])


@pytest.fixture
def env(isolated_db):
    from models.bots import Bots
    from routes import chat, widget
    from utils.dependencies import get_agent_service

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"w-{uuid.uuid4().hex[:6]}", name="W")
    other = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"x-{uuid.uuid4().hex[:6]}", name="X")
    agent = _FakeAgent()
    app = FastAPI()
    app.include_router(widget.router)
    app.include_router(chat.router)
    app.dependency_overrides[get_agent_service] = lambda: agent
    client = TestClient(app)

    def token_for(b, user_id: str, ttl: int | None = None) -> str:
        from services.auth.widget_hmac import issue

        return issue(
            bot_id=b.id, user_id=user_id, session_id="s", key_version=b.widget_key_version, ttl_seconds=ttl
        )

    return {"bot": bot, "other": other, "client": client, "agent": agent, "token_for": token_for}


def _auth(token: str, user: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    if user:
        h["X-Emly-UserID"] = user
    return h


def _chat_body(user="alice", session=None):
    return {
        "user_id": user,
        "session_id": session or f"session-{uuid.uuid4()}",
        "timestamp": 1,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }


# ------------------------------------------------------------------ init


def test_init_issues_token_bound_to_user(env):
    r = env["client"].post(f"/widget/{env['bot'].id}/init", json={"user_id": "alice"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "alice"
    r2 = env["client"].get(f"/widget/{env['bot'].id}/submissions/counts", headers=_auth(body["token"], "alice"))
    assert r2.status_code == 200, r2.text


# ------------------------------------------------- rejection matrix per route


def _call(env, route: str, headers: dict, user="alice"):
    c, bot = env["client"], env["bot"]
    if route == "chat":
        return c.post(f"/widget/{bot.id}/chat", headers=headers, json=_chat_body(user))
    if route == "counts":
        return c.get(f"/widget/{bot.id}/submissions/counts", headers=headers)
    if route == "impressions":
        return c.post(f"/widget/{bot.id}/impressions", headers=headers, json={"type": "short"})
    if route == "action":
        payload = {"user_id": user, "session_id": "s1", "action_name": "click", "action_value": "x"}
        return c.post(f"/widget/{bot.id}/action", headers=headers, data={"payload": json.dumps(payload)})
    if route == "rate":
        return c.post(
            f"/widget/{bot.id}/messages/1/rate", headers=headers, json={"rating": 1, "session_id": "s1"}
        )
    raise AssertionError(route)


ROUTES = ["chat", "counts", "impressions", "action", "rate"]


@pytest.mark.parametrize("route", ROUTES)
def test_no_token_is_401(env, route):
    r = _call(env, route, {"X-Emly-UserID": "alice"})
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["code"] == "widget_token_missing"


@pytest.mark.parametrize("route", ROUTES)
def test_token_for_other_bot_is_401(env, route):
    r = _call(env, route, _auth(env["token_for"](env["other"], "alice"), "alice"))
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["code"] == "widget_token_invalid"


@pytest.mark.parametrize("route", ROUTES)
def test_token_for_other_user_is_401(env, route):
    r = _call(env, route, _auth(env["token_for"](env["bot"], "mallory"), "alice"))
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["code"] == "widget_user_mismatch"


@pytest.mark.parametrize("route", ROUTES)
def test_expired_token_is_401(env, route):
    r = _call(env, route, _auth(env["token_for"](env["bot"], "alice", ttl=-1), "alice"))
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["code"] == "widget_token_invalid"


@pytest.mark.parametrize("route", ROUTES)
def test_tampered_token_is_401(env, route):
    token = env["token_for"](env["bot"], "alice")
    payload, sig = token.rsplit(".", 1)
    # First signature char is fully significant (the last one has padding bits).
    tampered = payload + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
    r = _call(env, route, _auth(tampered, "alice"))
    assert r.status_code == 401, r.text


# ----------------------------------------------- body user_id must match too


def test_chat_body_user_mismatch_is_401(env):
    token = env["token_for"](env["bot"], "alice")
    r = env["client"].post(f"/widget/{env['bot'].id}/chat", headers=_auth(token), json=_chat_body("mallory"))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "widget_user_mismatch"
    assert env["agent"].calls == []


def test_chat_with_valid_token_reaches_agent(env):
    token = env["token_for"](env["bot"], "alice")
    r = env["client"].post(f"/widget/{env['bot'].id}/chat", headers=_auth(token, "alice"), json=_chat_body("alice"))
    assert r.status_code == 200, r.text
    assert env["agent"].calls and env["agent"].calls[0]["user_id"] == "alice"


def test_chat_refuses_another_users_session(env):
    from models.emly_sessions import EMLYSessions

    session_id = f"session-{uuid.uuid4()}"
    EMLYSessions.upsert_on_message(
        bot_id=env["bot"].id, session_id=session_id, user_id="bob", channel_id=None,
        ts=datetime.now(timezone.utc),
    )
    token = env["token_for"](env["bot"], "alice")
    r = env["client"].post(
        f"/widget/{env['bot'].id}/chat", headers=_auth(token, "alice"), json=_chat_body("alice", session_id)
    )
    assert r.status_code == 403
    assert env["agent"].calls == []


def test_action_body_user_mismatch_is_401(env):
    token = env["token_for"](env["bot"], "alice")
    payload = {"user_id": "mallory", "session_id": "s1", "action_name": "click"}
    r = env["client"].post(
        f"/widget/{env['bot'].id}/action", headers=_auth(token), data={"payload": json.dumps(payload)}
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "widget_user_mismatch"


def test_action_otp_uses_email_but_checks_visitor(env, monkeypatch):
    """OTP actions carry the email as user_id; the visitor_id must match."""
    from routes import widget as widget_routes

    monkeypatch.setattr(widget_routes, "create_otp", lambda payload, bot_id: None)
    monkeypatch.setattr(widget_routes, "email_enabled", lambda: True)
    token = env["token_for"](env["bot"], "alice")
    ok = {"user_id": "alice@example.com", "form_title": "auth_authentication", "visitor_id": "alice"}
    r = env["client"].post(
        f"/widget/{env['bot'].id}/action", headers=_auth(token, "alice"), data={"payload": json.dumps(ok)}
    )
    assert r.status_code == 200, r.text
    bad = {**ok, "visitor_id": "mallory"}
    r = env["client"].post(
        f"/widget/{env['bot'].id}/action", headers=_auth(token, "alice"), data={"payload": json.dumps(bad)}
    )
    assert r.status_code == 401


def test_rate_refuses_another_users_message(env):
    from models.emly_messages import EMLYMessages

    msg = EMLYMessages.insert_new_message(
        bot_id=env["bot"].id, user_id="bob", session_id="s-bob", role="assistant",
        message="hi", not_useful=False, expanded_query=None, page=None,
    )
    message_id = msg["id"] if isinstance(msg, dict) else msg.id
    token = env["token_for"](env["bot"], "alice")
    r = env["client"].post(
        f"/widget/{env['bot'].id}/messages/{message_id}/rate",
        headers=_auth(token, "alice"),
        json={"rating": 1, "session_id": "s-bob"},
    )
    assert r.status_code == 403
    # The owner can rate it.
    token_bob = env["token_for"](env["bot"], "bob")
    r = env["client"].post(
        f"/widget/{env['bot'].id}/messages/{message_id}/rate",
        headers=_auth(token_bob, "bob"),
        json={"rating": 1, "session_id": "s-bob"},
    )
    assert r.status_code == 200, r.text


def test_rotated_key_rejects_old_token_after_grace(env, monkeypatch):
    from models.bots import Bots

    token = env["token_for"](env["bot"], "alice")
    Bots.rotate_widget_key(env["bot"].id)
    monkeypatch.setenv("WIDGET_TOKEN_ROTATION_GRACE_SECONDS", "0")
    r = env["client"].get(f"/widget/{env['bot'].id}/submissions/counts", headers=_auth(token, "alice"))
    assert r.status_code == 401
