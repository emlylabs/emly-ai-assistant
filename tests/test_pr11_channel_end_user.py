"""PR #11: chat-app senders get an ``emly_user`` row on first contact.

``channels.dispatcher._ensure_end_user`` runs on every inbound channel
message. It must create the bot-user row the first time a sender is seen,
leave it alone afterwards, and never let a lost insert race (another
worker created the row between our SELECT and INSERT) break the reply.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _forget_known_users():
    from channels import dispatcher

    dispatcher._KNOWN_END_USERS.clear()
    yield
    dispatcher._KNOWN_END_USERS.clear()


def _channel(bot_id: str, type_: str = "slack"):
    from models.bot_channels import BotChannelModel

    return BotChannelModel(
        id=f"ch-{uuid.uuid4().hex[:8]}",
        bot_id=bot_id,
        type=type_,
        external_id="T123",
        is_active=True,
        created_at=datetime.now(),
    )


@pytest.fixture
def bot(isolated_db):
    from models.bots import Bot, Bots
    from models.emly_users import EMLYUser

    b = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"eu-{uuid.uuid4().hex[:6]}", name="EU")
    yield b
    EMLYUser.delete().where(EMLYUser.bot == b.id).execute()
    Bot.delete().where(Bot.id == b.id).execute()


def _rows(bot_id: str, user_id: str):
    from models.emly_users import EMLYUser

    return list(EMLYUser.select().where((EMLYUser.bot == bot_id) & (EMLYUser.id == user_id)))


def test_first_inbound_message_creates_end_user(bot):
    from channels.dispatcher import _ensure_end_user
    from models.emly_users import EMLYUsers

    channel = _channel(bot.id, "slack")
    user_id = f"emly-{uuid.uuid4().hex}"
    assert EMLYUsers.get_user_by_id(bot.id, user_id) is None

    _ensure_end_user(bot.id, user_id, channel)

    user = EMLYUsers.get_user_by_id(bot.id, user_id)
    assert user is not None
    assert user.bot_id == bot.id
    assert user.browser == ""  # not a browser; the channel lives in meta
    assert user.meta == {"channel": "slack", "channel_id": channel.id}
    assert EMLYUsers.count(bot.id) == 1


def test_second_inbound_message_does_not_duplicate_or_overwrite(bot, monkeypatch):
    from channels.dispatcher import _ensure_end_user
    from models.emly_users import EMLYUsers

    channel = _channel(bot.id, "google_chat")
    user_id = f"emly-{uuid.uuid4().hex}"
    _ensure_end_user(bot.id, user_id, channel)
    first = EMLYUsers.get_user_by_id(bot.id, user_id)

    inserts: list = []
    real_insert = EMLYUsers.insert_new_user
    monkeypatch.setattr(
        EMLYUsers, "insert_new_user", lambda **kw: inserts.append(kw) or real_insert(**kw)
    )
    # Same sender, but via a different install: the existing row wins.
    _ensure_end_user(bot.id, user_id, _channel(bot.id, "slack"))

    assert inserts == [], "an existing end-user must not trigger another insert"
    assert len(_rows(bot.id, user_id)) == 1
    again = EMLYUsers.get_user_by_id(bot.id, user_id)
    assert again.meta == first.meta == {"channel": "google_chat", "channel_id": again.meta["channel_id"]}
    assert again.timestamp == first.timestamp


def test_lost_insert_race_is_swallowed_and_keeps_winner_row(bot, monkeypatch):
    """Simulate another worker inserting the row after our existence check:
    the SELECT reports "missing", the INSERT then hits the composite PK.
    ``_ensure_end_user`` logs and returns; it must not raise, and the row
    the other worker wrote is left as-is."""
    from peewee import IntegrityError

    from channels.dispatcher import _ensure_end_user
    from models.emly_users import EMLYUsers

    user_id = f"emly-{uuid.uuid4().hex}"
    # The "other worker" wins the race.
    EMLYUsers.insert_new_user(
        bot_id=bot.id, id=user_id, first_name="Winner", last_name=None, email=None,
        phone=None, ip="", browser="google_chat", meta={"channel": "google_chat"},
    )

    # Our SELECT ran before their INSERT committed, so it saw nothing.
    monkeypatch.setattr(EMLYUsers, "get_user_by_id", lambda bot_id, id: None)
    raised: list = []
    real_insert = EMLYUsers.insert_new_user

    def _spy_insert(**kw):
        try:
            return real_insert(**kw)
        except Exception as e:
            raised.append(e)
            raise

    monkeypatch.setattr(EMLYUsers, "insert_new_user", _spy_insert)

    _ensure_end_user(bot.id, user_id, _channel(bot.id, "slack"))  # must not raise

    # The conflict path was really exercised (not short-circuited).
    assert len(raised) == 1 and isinstance(raised[0], IntegrityError)
    rows = _rows(bot.id, user_id)
    assert len(rows) == 1
    assert rows[0].first_name == "Winner"
    assert rows[0].browser == "google_chat"


def test_unexpected_db_error_does_not_propagate(bot, monkeypatch):
    """Best-effort: any failure creating the user must not stop the reply."""
    from channels.dispatcher import _ensure_end_user
    from models.emly_users import EMLYUsers

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(EMLYUsers, "get_user_by_id", _boom)
    _ensure_end_user(bot.id, f"emly-{uuid.uuid4().hex}", _channel(bot.id))


def test_known_sender_skips_the_database(bot, monkeypatch):
    """After the first message, later messages from the same sender in this
    process don't query for the user at all (it ran on every message)."""
    from channels.dispatcher import _ensure_end_user
    from models.emly_users import EMLYUsers

    channel = _channel(bot.id, "slack")
    user_id = f"emly-{uuid.uuid4().hex}"
    _ensure_end_user(bot.id, user_id, channel)

    lookups: list = []
    monkeypatch.setattr(EMLYUsers, "get_user_by_id", lambda *a, **kw: lookups.append(a) or None)
    _ensure_end_user(bot.id, user_id, channel)
    assert lookups == []


def test_failed_attempt_is_retried_on_the_next_message(bot, monkeypatch):
    from channels.dispatcher import _ensure_end_user
    from models.emly_users import EMLYUsers

    user_id = f"emly-{uuid.uuid4().hex}"
    real_get = EMLYUsers.get_user_by_id

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(EMLYUsers, "get_user_by_id", _boom)
    _ensure_end_user(bot.id, user_id, _channel(bot.id))
    monkeypatch.setattr(EMLYUsers, "get_user_by_id", real_get)
    _ensure_end_user(bot.id, user_id, _channel(bot.id))
    assert EMLYUsers.get_user_by_id(bot.id, user_id) is not None


def test_known_sender_is_rechecked_after_the_ttl(bot, monkeypatch):
    """Data retention can delete the row; a returning sender gets it back."""
    from channels import dispatcher
    from models.emly_users import EMLYUser, EMLYUsers

    channel = _channel(bot.id, "slack")
    user_id = f"emly-{uuid.uuid4().hex}"
    dispatcher._ensure_end_user(bot.id, user_id, channel)
    EMLYUser.delete().where((EMLYUser.bot == bot.id) & (EMLYUser.id == user_id)).execute()

    dispatcher._ensure_end_user(bot.id, user_id, channel)
    assert EMLYUsers.get_user_by_id(bot.id, user_id) is None  # still cached

    key = (bot.id, user_id)
    dispatcher._KNOWN_END_USERS[key] -= dispatcher._KNOWN_END_USER_TTL_S + 1
    dispatcher._ensure_end_user(bot.id, user_id, channel)
    assert EMLYUsers.get_user_by_id(bot.id, user_id) is not None
