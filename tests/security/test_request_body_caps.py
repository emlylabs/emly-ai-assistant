"""S2: every request body is capped before anything parses it.

Before: only ``/widget/...`` bodies were capped (at 110 MB), the chat
middleware decoded and re-serialised each chat body on the event loop
before the token was checked, channel webhooks and admin routes had no
cap at all, and an over-limit chunked body came back as 500 because the
middleware swallowed the limit's exception.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from services import request_limits

KB = 1024
MB = 1024 * 1024


def _chunks(total: int, size: int = 64 * KB):
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        sent += n
        yield b"x" * n


@pytest.fixture
def app_client():
    import main

    return TestClient(main.app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", [
    "/widget/no-such-bot/chat",
    "/api/channels/slack/webhook/abc",
    "/api/admin/bots/x/config",
])
def test_declared_oversize_body_refused_anywhere(app_client, path):
    r = app_client.post(path, content=b"x" * (request_limits.request_max_bytes() + 1),
                        headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_chunked_oversize_chat_body_is_413_not_500(app_client):
    r = app_client.post("/widget/no-such-bot/chat",
                        content=_chunks(request_limits.request_max_bytes() + 1),
                        headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_limits_per_path(monkeypatch):
    for name in ("REQUEST_MAX_KB", "WIDGET_MAX_REQUEST_MB", "ADMIN_UPLOAD_MAX_MB"):
        monkeypatch.delenv(name, raising=False)
    assert request_limits.limit_for("POST", "/widget/b/chat") == (1024 * KB, "REQUEST_MAX_KB")
    assert request_limits.limit_for("POST", "/widget/b/action") == (110 * MB, "WIDGET_MAX_REQUEST_MB")
    assert request_limits.limit_for("POST", "/api/admin/bots/s/files") == (1024 * MB, "ADMIN_UPLOAD_MAX_MB")
    # Only the upload routes themselves get the big limits.
    assert request_limits.limit_for("PATCH", "/api/admin/bots/s/files/f1")[1] == "REQUEST_MAX_KB"
    assert request_limits.limit_for("POST", "/widget/b/impressions")[1] == "REQUEST_MAX_KB"

    monkeypatch.setenv("REQUEST_MAX_KB", "64")
    assert request_limits.limit_for("POST", "/x")[0] == 64 * KB


# ------------------------------------------------------------------ chat route
from tests.security.test_widget_token import _auth, env  # noqa: E402,F401  (fixture)


def _body(**over):
    body = {"messages": [{"role": "user", "content": "hello"}], "stream": False}
    body.update(over)
    return body


def test_overlong_message_is_refused(env):  # noqa: F811
    from utils.schemas import MAX_MESSAGE_CHARS

    token = env["token_for"](env["bot"], "alice")
    r = env["client"].post(f"/widget/{env['bot'].id}/chat", headers=_auth(token, "alice"),
                           json=_body(messages=[{"role": "user", "content": "x" * (MAX_MESSAGE_CHARS + 1)}]))
    assert r.status_code == 422
    assert env["agent"].calls == []


def test_ids_come_from_headers_and_a_session_is_minted(env):  # noqa: F811
    token = env["token_for"](env["bot"], "alice")
    r = env["client"].post(f"/widget/{env['bot'].id}/chat",
                           headers={**_auth(token, "alice"), "X-Emly-PageID": "p1"}, json=_body())
    assert r.status_code == 200, r.text
    minted = r.headers["X-Emly-SessionID"]
    assert minted.startswith("session-")
    (call,) = env["agent"].calls
    assert call["user_id"] == "alice" and call["session_id"] == minted and call["page_id"] == "p1"


def test_header_session_id_wins(env):  # noqa: F811
    token = env["token_for"](env["bot"], "alice")
    sid = f"session-{uuid.uuid4()}"
    r = env["client"].post(f"/widget/{env['bot'].id}/chat",
                           headers={**_auth(token, "alice"), "X-Emly-SessionID": sid},
                           json=_body(session_id="session-from-body"))
    assert r.status_code == 200, r.text
    assert env["agent"].calls[0]["session_id"] == sid
    assert r.headers["X-Emly-SessionID"] == sid
