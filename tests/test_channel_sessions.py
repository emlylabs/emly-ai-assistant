"""How messaging-channel traffic maps onto sessions.

Adapters key a conversation on a platform id (a Slack DM, a Chat thread).
For a running conversation (DM, group chat) the dispatcher rotates that key
through ``EMLYSessions.current_channel_session`` so a resolved or idle
conversation doesn't absorb every later question from the same person; a
thread keeps one session for life. A Slack / Google Chat DM conversation
that was open when DMs were re-keyed (flagged by migration 012) carries on.
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

    from models.emly_sessions import EMLYSessionsTable

    DB.connect(reuse_if_open=True)
    EMLYSessionsTable._carryover_done = False
    EMLYSessionsTable._carryover_swept_at = None
    bid = f"bot-{uuid.uuid4().hex[:8]}"
    Bots.insert(id=bid, slug=f"s-{bid}", name="B")
    yield bid
    EMLYMessage.delete().where(EMLYMessage.bot == bid).execute()
    EMLYSession.delete().where(EMLYSession.bot_id == bid).execute()
    EMLYSessionsTable._carryover_done = False
    EMLYSessionsTable._carryover_swept_at = None


def _session(bot_id, sid, *, last_activity, is_resolved=None, ended_at=None,
             user_id="u", channel_id=None, dm_carryover=None):
    from models.emly_sessions import EMLYSession

    EMLYSession.create(
        id=sid, bot_id=bot_id, user_id=user_id, channel_id=channel_id,
        started_at=last_activity, last_activity_at=last_activity,
        turn_count=1, is_resolved=is_resolved, ended_at=ended_at,
        dm_carryover=dm_carryover,
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


def test_idle_rotation_closes_the_abandoned_session(bot_id):
    """The superseded session leaves the open list without being counted
    as resolved."""
    from models.emly_sessions import EMLYSessions

    now = datetime(2026, 11, 1, 12)
    _session(bot_id, "base", last_activity=now - IDLE - timedelta(hours=1))
    assert _current(bot_id, "base", now) == "base-1"
    old = EMLYSessions.get(bot_id, "base")
    assert old.ended_at == now and old.is_resolved is None
    _session(bot_id, "base-1", last_activity=now)
    unresolved = {s.id for s in EMLYSessions.list_for_bot(bot_id, is_resolved=False)}
    resolved = {s.id for s in EMLYSessions.list_for_bot(bot_id, is_resolved=True)}
    assert unresolved == {"base-1"} and resolved == set()


def test_reopening_a_closed_session_puts_it_back_on_the_open_list(bot_id):
    from models.emly_sessions import EMLYSessions

    now = datetime(2026, 11, 1, 12)
    _session(bot_id, "base", last_activity=now - IDLE * 2)
    _current(bot_id, "base", now)
    EMLYSessions.mark_resolved(bot_id, "base", resolved_by="admin", is_resolved=False)
    assert "base" in {s.id for s in EMLYSessions.list_for_bot(bot_id, is_resolved=False)}


def test_closed_session_rotates(bot_id):
    now = datetime(2026, 11, 1, 12)
    _session(bot_id, "base", last_activity=now - timedelta(minutes=5), ended_at=now - timedelta(minutes=1))
    assert _current(bot_id, "base", now) == "base-1"


def test_new_activity_reopens_a_closed_session(bot_id):
    from models.emly_messages import EMLYMessages
    from models.emly_sessions import EMLYSessions

    now = datetime.utcnow()
    _session(bot_id, "closed", last_activity=now - IDLE * 2, ended_at=now - IDLE)
    _session(bot_id, "done", last_activity=now, is_resolved=True, ended_at=now)
    EMLYMessages.insert_new_message(bot_id, "u", "closed", "user", "back again", False, None, None)
    EMLYMessages.insert_new_message(bot_id, "u", "done", "user", "one more", False, None, None)
    assert EMLYSessions.get(bot_id, "closed").ended_at is None
    assert EMLYSessions.get(bot_id, "done").ended_at is not None, "a resolved session stays resolved"


# ---------------------------------------------------------------------------
# Dispatcher: which conversations rotate, and the DM carry-over
# ---------------------------------------------------------------------------
USER = "user-1"
CH = "ch-1"


def _incoming(session_key, chat_type):
    from channels.contracts import IncomingMessage

    return IncomingMessage(
        channel_id=CH, user_external_id="U1", session_external_id=session_key,
        text="hi", chat_type=chat_type,
    )


def _resolve(bot_id, incoming, user_id=USER, channel_id=CH):
    from channels.dispatcher import _resolve_session_id

    return _resolve_session_id(bot_id, "T1", user_id, channel_id, incoming)


def _sid(bot_id, key):
    from channels.dispatcher import emly_id_for

    return emly_id_for(bot_id, "T1", key)


def _flagged(bot_id, sid, *, last_activity, **kw):
    kw.setdefault("user_id", USER)
    kw.setdefault("channel_id", CH)
    _session(bot_id, sid, last_activity=last_activity, dm_carryover=True, **kw)


def _flag(bot_id, sid):
    from models.emly_sessions import EMLYSessions

    return EMLYSessions.get(bot_id, sid).dm_carryover


@pytest.mark.parametrize("chat_type", ["thread", "channel"])
def test_thread_keeps_its_session_after_a_long_gap(bot_id, chat_type):
    """A next-day reply in the same Slack thread sees the earlier turns."""
    base = _sid(bot_id, "C1:100.1")
    _session(bot_id, base, last_activity=datetime.utcnow() - timedelta(days=3))
    assert _resolve(bot_id, _incoming("C1:100.1", chat_type)) == base


def test_resolved_thread_keeps_its_session(bot_id):
    base = _sid(bot_id, "C1:100.1")
    _session(bot_id, base, last_activity=datetime.utcnow(), is_resolved=True)
    assert _resolve(bot_id, _incoming("C1:100.1", "thread")) == base


@pytest.mark.parametrize("chat_type", ["dm", "group"])
def test_running_conversation_rotates_after_idle(bot_id, chat_type):
    base = _sid(bot_id, "D1:U1")
    _session(bot_id, base, last_activity=datetime.utcnow() - timedelta(days=3))
    assert _resolve(bot_id, _incoming("D1:U1", chat_type)) == f"{base}-1"


def test_next_dm_message_continues_the_open_pre_upgrade_session(bot_id):
    """The reviewer's case: the person just sends their next DM (a new
    top-level Slack message, a new Google Chat thread) and the lead in
    progress carries on."""
    old = _sid(bot_id, "D1:100.1")  # pre-upgrade per-thread key
    _flagged(bot_id, old, last_activity=datetime.utcnow() - timedelta(minutes=10))
    assert _resolve(bot_id, _incoming("D1:U1", "dm")) == old
    assert _resolve(bot_id, _incoming("D1:U1", "dm")) == old, "and keeps doing so while open"


def test_most_recent_open_pre_upgrade_session_wins(bot_id):
    now = datetime.utcnow()
    _flagged(bot_id, "older", last_activity=now - timedelta(hours=3))
    _flagged(bot_id, "newer", last_activity=now - timedelta(minutes=3))
    assert _resolve(bot_id, _incoming("D1:U1", "dm")) == "newer"


def test_idle_pre_upgrade_session_is_closed_and_the_dm_moves_on(bot_id):
    from models.emly_sessions import EMLYSessions

    old = "old-thread"
    _flagged(bot_id, old, last_activity=datetime.utcnow() - IDLE - timedelta(hours=1))
    assert _resolve(bot_id, _incoming("D1:U1", "dm")) == _sid(bot_id, "D1:U1")
    row = EMLYSessions.get(bot_id, old)
    assert row.ended_at is not None and row.is_resolved is None
    assert row.dm_carryover is None
    assert old not in {s.id for s in EMLYSessions.list_for_bot(bot_id, is_resolved=False)}


def test_abandoned_pre_upgrade_session_is_closed_without_its_sender(bot_id):
    """The sender never writes again: the sweep still closes the session
    once it is idle, so it doesn't stay on the open list."""
    from models.emly_sessions import EMLYSessions

    _flagged(bot_id, "abandoned", last_activity=datetime.utcnow() - IDLE - timedelta(hours=1), user_id="gone")
    _flagged(bot_id, "active", last_activity=datetime.utcnow(), user_id="still-here")
    EMLYSessions.take_dm_carryover(bot_id, "someone", CH, idle_after=IDLE)
    gone = EMLYSessions.get(bot_id, "abandoned")
    assert gone.ended_at is not None and gone.dm_carryover is None
    active = EMLYSessions.get(bot_id, "active")
    assert active.ended_at is None and active.dm_carryover is True


def test_resolved_pre_upgrade_session_is_not_continued(bot_id):
    _flagged(bot_id, "old", last_activity=datetime.utcnow(), is_resolved=True)
    assert _resolve(bot_id, _incoming("D1:U1", "dm")) == _sid(bot_id, "D1:U1")
    assert _flag(bot_id, "old") is None


def test_other_senders_and_installs_are_not_continued(bot_id):
    now = datetime.utcnow()
    _flagged(bot_id, "someone-else", last_activity=now, user_id="user-2")
    _flagged(bot_id, "other-install", last_activity=now, channel_id="ch-2")
    assert _resolve(bot_id, _incoming("D1:U1", "dm")) == _sid(bot_id, "D1:U1")


def test_unflagged_session_is_not_continued(bot_id):
    """Sessions started after the deploy (e.g. a channel thread) are never
    pulled into a DM."""
    _session(bot_id, "thread-after-deploy", last_activity=datetime.utcnow(), user_id=USER, channel_id=CH)
    assert _resolve(bot_id, _incoming("D1:U1", "dm")) == _sid(bot_id, "D1:U1")


def test_multi_person_pre_upgrade_thread_is_skipped_not_closed(bot_id):
    """A flagged session with turns from someone else is a channel thread:
    leave it alone (still open), just drop the flag."""
    from models.emly_messages import EMLYMessages
    from models.emly_sessions import EMLYSessions

    _flagged(bot_id, "shared-thread", last_activity=datetime.utcnow())
    EMLYMessages.insert_new_message(bot_id, USER, "shared-thread", "user", "q", False, None, None)
    EMLYMessages.insert_new_message(bot_id, "user-2", "shared-thread", "user", "me too", False, None, None)
    assert _resolve(bot_id, _incoming("D1:U1", "dm")) == _sid(bot_id, "D1:U1")
    row = EMLYSessions.get(bot_id, "shared-thread")
    assert row.ended_at is None and row.dm_carryover is None


def test_group_chat_does_not_use_the_carry_over(bot_id):
    _flagged(bot_id, "old", last_activity=datetime.utcnow())
    assert _resolve(bot_id, _incoming("G1", "group")) == _sid(bot_id, "G1")


def test_carry_over_stops_querying_once_nothing_is_left(bot_id, monkeypatch):
    from models.emly_sessions import EMLYSession, EMLYSessions

    EMLYSession.update(dm_carryover=None).where(EMLYSession.dm_carryover == True).execute()  # noqa: E712
    assert EMLYSessions.take_dm_carryover(bot_id, USER, CH, idle_after=IDLE) is None
    assert EMLYSessions._carryover_done
    calls = []
    monkeypatch.setattr(EMLYSession, "select", lambda *a, **kw: calls.append(a))
    assert EMLYSessions.take_dm_carryover(bot_id, USER, CH, idle_after=IDLE) is None
    assert calls == []


def test_migration_flags_only_open_slack_and_gchat_sessions(bot_id):
    import importlib

    from db.db import DB
    from models.bot_channels import BotChannel

    mig = importlib.import_module("migrations.012_channel_dm_carryover")
    now = datetime.utcnow()
    for cid, type_ in (("c-slack", "slack"), ("c-gchat", "google_chat"), ("c-tg", "telegram")):
        BotChannel.create(id=f"{bot_id}-{cid}", bot=bot_id, type=type_, created_at=now)
    try:
        _session(bot_id, "slack-open", last_activity=now, channel_id=f"{bot_id}-c-slack")
        _session(bot_id, "gchat-open", last_activity=now, channel_id=f"{bot_id}-c-gchat")
        _session(bot_id, "slack-reopened", last_activity=now, channel_id=f"{bot_id}-c-slack", is_resolved=False)
        _session(bot_id, "slack-resolved", last_activity=now, channel_id=f"{bot_id}-c-slack", is_resolved=True)
        _session(bot_id, "slack-ended", last_activity=now, channel_id=f"{bot_id}-c-slack", ended_at=now)
        _session(bot_id, "slack-idle", last_activity=now - IDLE * 2, channel_id=f"{bot_id}-c-slack")
        _session(bot_id, "telegram-open", last_activity=now, channel_id=f"{bot_id}-c-tg")
        _session(bot_id, "widget-open", last_activity=now)
        mig._flag_open_sessions(DB)
        flagged = {
            sid for sid in ("slack-open", "gchat-open", "slack-reopened", "slack-resolved",
                            "slack-ended", "slack-idle", "telegram-open", "widget-open")
            if _flag(bot_id, sid)
        }
        assert flagged == {"slack-open", "gchat-open", "slack-reopened"}
    finally:
        BotChannel.delete().where(BotChannel.bot == bot_id).execute()
