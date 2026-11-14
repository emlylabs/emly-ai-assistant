"""How messaging-channel traffic maps onto sessions.

Adapters key a conversation on a platform id (a Slack DM, a Chat thread);
the dispatcher then rotates that key through
``EMLYSessions.current_channel_session`` so a resolved or idle
conversation doesn't absorb every later question from the same person.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta

import pytest
from starlette.requests import Request

IDLE = timedelta(hours=24)


def _request(body: dict) -> Request:
    req = Request({"type": "http", "method": "POST", "headers": []})
    req._body = json.dumps(body).encode()
    return req


# ---------------------------------------------------------------------------
# Adapter keying
# ---------------------------------------------------------------------------
def _slack(event: dict):
    from channels.slack import SlackAdapter, SlackSecrets

    secrets = SlackSecrets(access_token="x", signing_secret="y", bot_user_id="UBOT")
    body = {"type": "event_callback", "event": {"type": "message", "user": "U1", "text": "hi", **event}}
    return asyncio.run(SlackAdapter().parse_inbound(_request(body), secrets))


def test_slack_dm_messages_share_one_session_key():
    a = _slack({"channel": "D1", "channel_type": "im", "ts": "100.1"})
    b = _slack({"channel": "D1", "channel_type": "im", "ts": "200.1"})
    assert a.session_external_id == b.session_external_id == "D1:U1"
    assert a.reply_handle["thread_ts"] == "100.1"


def test_slack_channel_thread_replies_join_the_root_session():
    root = _slack({"channel": "C1", "channel_type": "channel", "ts": "100.1"})
    reply = _slack({"channel": "C1", "channel_type": "channel", "ts": "150.1", "thread_ts": "100.1"})
    other_user_reply = _slack({
        "channel": "C1", "channel_type": "channel", "ts": "160.1", "thread_ts": "100.1", "user": "U2",
    })
    assert root.session_external_id == reply.session_external_id == other_user_reply.session_external_id
    assert reply.chat_type == "thread"


def test_slack_separate_channel_threads_are_separate_sessions():
    a = _slack({"channel": "C1", "channel_type": "channel", "ts": "100.1"})
    b = _slack({"channel": "C1", "channel_type": "channel", "ts": "300.1"})
    assert a.session_external_id != b.session_external_id


def _gchat(space: dict, thread: str | None, user: str = "a@example.com"):
    from channels.google_chat import GoogleChatAdapter, GoogleChatSecrets

    secrets = GoogleChatSecrets(service_account_json={}, service_account_email="bot@example.com")
    msg = {"sender": {"type": "HUMAN", "email": user}, "text": "hi", "space": space}
    if thread:
        msg["thread"] = {"name": thread}
    return asyncio.run(GoogleChatAdapter().parse_inbound(_request({"type": "MESSAGE", "message": msg}), secrets))


def test_gchat_dm_ignores_per_message_threads():
    space = {"name": "spaces/DM1", "type": "DM"}
    a = _gchat(space, "spaces/DM1/threads/t1")
    b = _gchat(space, "spaces/DM1/threads/t2")
    assert a.session_external_id == b.session_external_id
    assert a.reply_handle["thread_name"] == "spaces/DM1/threads/t1"


def test_gchat_room_threads_are_separate_sessions():
    space = {"name": "spaces/ROOM", "type": "ROOM"}
    a = _gchat(space, "spaces/ROOM/threads/t1")
    a2 = _gchat(space, "spaces/ROOM/threads/t1", user="b@example.com")
    b = _gchat(space, "spaces/ROOM/threads/t2")
    assert a.session_external_id == a2.session_external_id
    assert a.session_external_id != b.session_external_id


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------
@pytest.fixture
def bot_id():
    from db.db import DB
    from models.bots import Bots
    from models.emly_messages import EMLYMessage
    from models.emly_sessions import EMLYSession

    DB.connect(reuse_if_open=True)
    bid = f"bot-{uuid.uuid4().hex[:8]}"
    Bots.insert(id=bid, slug=f"s-{bid}", name="B")
    yield bid
    EMLYMessage.delete().where(EMLYMessage.bot == bid).execute()
    EMLYSession.delete().where(EMLYSession.bot_id == bid).execute()


def _session(bot_id, sid, *, last_activity, is_resolved=None):
    from models.emly_sessions import EMLYSession

    EMLYSession.create(
        id=sid, bot_id=bot_id, user_id="u", channel_id=None,
        started_at=last_activity, last_activity_at=last_activity,
        turn_count=1, is_resolved=is_resolved,
    )


def _current(bot_id, base, now):
    from models.emly_sessions import EMLYSessions

    return EMLYSessions.current_channel_session(bot_id, base, idle_after=IDLE, now=now)


def test_first_message_uses_base_id(bot_id):
    assert _current(bot_id, "base", datetime(2026, 11, 1)) == "base"


def test_active_session_is_reused(bot_id):
    now = datetime(2026, 11, 1, 12)
    _session(bot_id, "base", last_activity=now - timedelta(hours=1))
    assert _current(bot_id, "base", now) == "base"


def test_resolved_session_rotates(bot_id):
    now = datetime(2026, 11, 1, 12)
    _session(bot_id, "base", last_activity=now - timedelta(minutes=5), is_resolved=True)
    assert _current(bot_id, "base", now) == "base-1"


def test_unresolved_marked_false_does_not_rotate(bot_id):
    now = datetime(2026, 11, 1, 12)
    _session(bot_id, "base", last_activity=now - timedelta(minutes=5), is_resolved=False)
    assert _current(bot_id, "base", now) == "base"


def test_idle_session_rotates(bot_id):
    now = datetime(2026, 11, 1, 12)
    _session(bot_id, "base", last_activity=now - IDLE - timedelta(seconds=1))
    assert _current(bot_id, "base", now) == "base-1"


def test_latest_generation_wins(bot_id):
    now = datetime(2026, 11, 1, 12)
    _session(bot_id, "base", last_activity=now - timedelta(days=30), is_resolved=True)
    _session(bot_id, "base-1", last_activity=now - timedelta(days=20), is_resolved=True)
    _session(bot_id, "base-2", last_activity=now - timedelta(hours=1))
    assert _current(bot_id, "base", now) == "base-2"
    _session(bot_id, "base-10", last_activity=now - timedelta(days=3))  # numeric, not lexical
    assert _current(bot_id, "base", now) == "base-11"


def test_unrelated_ids_and_other_bots_ignored(bot_id):
    now = datetime(2026, 11, 1, 12)
    _session(bot_id, "base-extra", last_activity=now, is_resolved=True)
    _session(bot_id, "base2", last_activity=now, is_resolved=True)
    other = f"{bot_id}-other"
    _session(other, "base", last_activity=now, is_resolved=True)
    try:
        assert _current(bot_id, "base", now) == "base"
    finally:
        from models.emly_sessions import EMLYSession
        EMLYSession.delete().where(EMLYSession.bot_id == other).execute()


def test_resolve_then_new_message_lands_in_a_new_unresolved_session(bot_id):
    """End to end through the real write path: the reviewer's B1 scenario."""
    from models.emly_messages import EMLYMessages
    from models.emly_sessions import EMLYSessions

    first = EMLYSessions.current_channel_session(bot_id, "dm-u1", idle_after=IDLE)
    EMLYMessages.insert_new_message(bot_id, "u1", first, "user", "hello", False, None, None)
    assert EMLYSessions.current_channel_session(bot_id, "dm-u1", idle_after=IDLE) == first

    EMLYSessions.mark_resolved(bot_id, first, resolved_by="admin")
    second = EMLYSessions.current_channel_session(bot_id, "dm-u1", idle_after=IDLE)
    assert second != first
    EMLYMessages.insert_new_message(bot_id, "u1", second, "user", "new question", False, None, None)

    unresolved = {s.id for s in EMLYSessions.list_for_bot(bot_id, is_resolved=False)}
    assert second in unresolved and first not in unresolved
