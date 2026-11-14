"""Conversations list filters in ``EMLYSessionsTable._apply_filters``."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest


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


def _session(bot_id, sid, *, user="u", channel_id=None, is_resolved=None):
    from models.emly_sessions import EMLYSession

    now = datetime(2026, 11, 1)
    EMLYSession.create(
        id=sid, bot_id=bot_id, user_id=user, channel_id=channel_id,
        started_at=now, last_activity_at=now + timedelta(minutes=1),
        turn_count=1, is_resolved=is_resolved,
    )


def _message(bot_id, sid, role, text):
    from models.emly_messages import EMLYMessage

    now = datetime(2026, 11, 1)
    EMLYMessage.create(
        bot=bot_id, user_id="u", session_id=sid, message=text, role=role,
        created_at=now, updated_at=now,
    )


def _ids(bot_id, **filters):
    from models.emly_sessions import EMLYSessions

    ids = {s.id for s in EMLYSessions.list_for_bot(bot_id, **filters)}
    assert EMLYSessions.count_for_bot(bot_id, **filters) == len(ids)
    return ids


def test_unresolved_filter_includes_never_marked(bot_id):
    _session(bot_id, "never", is_resolved=None)
    _session(bot_id, "reopened", is_resolved=False)
    _session(bot_id, "done", is_resolved=True)
    assert _ids(bot_id, is_resolved=False) == {"never", "reopened"}
    assert _ids(bot_id, is_resolved=True) == {"done"}


def test_web_widget_channel_maps_to_null_bucket(bot_id):
    from models.bot_channels import DEFAULT_WIDGET_CHANNEL_ID

    _session(bot_id, "widget", channel_id=None)
    _session(bot_id, "slack", channel_id="ch-slack")
    assert _ids(bot_id, channel_id=DEFAULT_WIDGET_CHANNEL_ID) == {"widget"}
    assert _ids(bot_id, channel_id="ch-slack") == {"slack"}


def test_search_matches_any_turn_and_ids(bot_id):
    _session(bot_id, "s-user-turn")
    _message(bot_id, "s-user-turn", "user", "Do you ship to Norway?")
    _session(bot_id, "s-assistant-turn")
    _message(bot_id, "s-assistant-turn", "assistant", "Our REFUND window is 30 days.")
    _session(bot_id, "abc123-session")
    _session(bot_id, "s-by-user", user="visitor-zz9")

    assert _ids(bot_id, search="norway") == {"s-user-turn"}
    assert _ids(bot_id, search="refund") == {"s-assistant-turn"}
    assert _ids(bot_id, search="abc123") == {"abc123-session"}
    assert _ids(bot_id, search="zz9") == {"s-by-user"}


def test_search_treats_like_wildcards_literally(bot_id):
    _session(bot_id, "pct")
    _message(bot_id, "pct", "user", "Is it 100% refundable?")
    _session(bot_id, "underscore")
    _message(bot_id, "underscore", "user", "my order_id is 7")
    _session(bot_id, "plain")
    _message(bot_id, "plain", "user", "orderXid")

    assert _ids(bot_id, search="%") == {"pct"}
    assert _ids(bot_id, search="order_id") == {"underscore"}


def test_search_is_scoped_to_the_bot(bot_id):
    from models.emly_messages import EMLYMessage
    from models.emly_sessions import EMLYSession

    other = f"{bot_id}-x"
    from models.bots import Bots
    Bots.insert(id=other, slug=f"s-{other}", name="X")
    try:
        _session(other, "foreign")
        _message(other, "foreign", "user", "secret pineapple")
        # Same session id under this bot, but its own messages don't match.
        _session(bot_id, "mine")
        _message(other, "mine", "user", "pineapple again")
        assert _ids(bot_id, search="pineapple") == set()
    finally:
        EMLYMessage.delete().where(EMLYMessage.bot == other).execute()
        EMLYSession.delete().where(EMLYSession.bot_id == other).execute()
