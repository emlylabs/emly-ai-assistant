"""PR #11: chat-app senders get an ``emly_user`` row on first contact.

``channels.dispatcher._ensure_end_user`` runs on every inbound channel
message. It must create the bot-user row the first time a sender is seen,
with the name / email / phone the platform gives, fill those in on a row
that has none yet, never overwrite a value already there, and never let
anything in this step (a lost insert race, a DB error, the profile
lookup, the cache) stop the reply.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Optional

import pytest

from channels.contracts import IncomingMessage, UserProfile


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


class _Adapter:
    """Stands in for a channel adapter's ``fetch_user_profile``."""

    def __init__(self, profile: Optional[UserProfile] = None, exc: Optional[Exception] = None):
        self.profile = profile
        self.exc = exc
        self.calls: list = []

    async def fetch_user_profile(self, channel, user_external_id):
        self.calls.append(user_external_id)
        if self.exc:
            raise self.exc
        return self.profile


def _ensure(bot_id, user_id, channel, *, profile=None, adapter=None):
    from channels.dispatcher import _ensure_end_user

    incoming = IncomingMessage(
        channel_id=channel.id, user_external_id="U1", session_external_id="s", text="hi",
        user_profile=profile or UserProfile(),
    )
    asyncio.run(_ensure_end_user(adapter or _Adapter(), bot_id, user_id, channel, incoming))


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


def _uid() -> str:
    return f"emly-{uuid.uuid4().hex}"


def test_first_inbound_message_creates_end_user(bot):
    from models.emly_users import EMLYUsers

    channel = _channel(bot.id, "slack")
    user_id = _uid()
    assert EMLYUsers.get_user_by_id(bot.id, user_id) is None

    _ensure(bot.id, user_id, channel)

    user = EMLYUsers.get_user_by_id(bot.id, user_id)
    assert user is not None
    assert user.bot_id == bot.id
    assert user.browser == ""  # not a browser; the channel lives in meta
    assert user.meta == {"channel": "slack", "channel_id": channel.id}
    assert EMLYUsers.count(bot.id) == 1


def test_new_end_user_gets_the_name_and_email_from_the_payload(bot):
    from models.emly_users import EMLYUsers

    user_id = _uid()
    adapter = _Adapter()
    _ensure(
        bot.id, user_id, _channel(bot.id, "google_chat"), adapter=adapter,
        profile=UserProfile(first_name="Ada", last_name="Lovelace", email="ada@example.com"),
    )
    user = EMLYUsers.get_user_by_id(bot.id, user_id)
    assert (user.first_name, user.last_name, user.email) == ("Ada", "Lovelace", "ada@example.com")
    assert adapter.calls == [], "no platform lookup when the payload already names the sender"


def test_platform_lookup_fills_in_when_the_payload_has_no_name(bot):
    """Slack events carry only the user id; the adapter looks the user up."""
    from models.emly_users import EMLYUsers

    user_id = _uid()
    adapter = _Adapter(UserProfile(first_name="Grace", last_name="Hopper", email="grace@example.com"))
    _ensure(bot.id, user_id, _channel(bot.id), adapter=adapter)
    user = EMLYUsers.get_user_by_id(bot.id, user_id)
    assert (user.first_name, user.email) == ("Grace", "grace@example.com")
    assert adapter.calls == ["U1"]


def test_failed_platform_lookup_still_creates_the_user(bot):
    from models.emly_users import EMLYUsers

    user_id = _uid()
    _ensure(bot.id, user_id, _channel(bot.id), adapter=_Adapter(exc=RuntimeError("slack down")))
    user = EMLYUsers.get_user_by_id(bot.id, user_id)
    assert user is not None and user.first_name is None


def test_nameless_row_from_an_earlier_message_is_filled_in(bot):
    """Rows created before names were captured get them on the next message."""
    from models.emly_users import EMLYUsers

    user_id = _uid()
    EMLYUsers.insert_new_user(
        bot_id=bot.id, id=user_id, first_name=None, last_name=None, email=None,
        phone="+15550100", ip="", browser="", meta={"channel": "whatsapp_cloud"},
    )
    _ensure(
        bot.id, user_id, _channel(bot.id, "whatsapp_cloud"),
        profile=UserProfile(first_name="Ada", phone="+19990000"),
    )
    user = EMLYUsers.get_user_by_id(bot.id, user_id)
    assert user.first_name == "Ada"
    assert user.phone == "+15550100", "a value already stored is never overwritten"
    assert len(_rows(bot.id, user_id)) == 1


def test_second_inbound_message_does_not_duplicate_or_overwrite(bot, monkeypatch):
    from models.emly_users import EMLYUsers

    channel = _channel(bot.id, "google_chat")
    user_id = _uid()
    _ensure(bot.id, user_id, channel, profile=UserProfile(first_name="Ada", email="ada@example.com"))
    first = EMLYUsers.get_user_by_id(bot.id, user_id)

    from channels import dispatcher
    dispatcher._KNOWN_END_USERS.clear()  # force the DB path
    inserts: list = []
    real_insert = EMLYUsers.insert_new_user
    monkeypatch.setattr(
        EMLYUsers, "insert_new_user", lambda **kw: inserts.append(kw) or real_insert(**kw)
    )
    # Same sender, but via a different install and a different name: the
    # existing row wins.
    _ensure(bot.id, user_id, _channel(bot.id, "slack"), profile=UserProfile(first_name="Someone"))

    assert inserts == [], "an existing end-user must not trigger another insert"
    assert len(_rows(bot.id, user_id)) == 1
    again = EMLYUsers.get_user_by_id(bot.id, user_id)
    assert again.first_name == "Ada"
    assert again.meta == first.meta == {"channel": "google_chat", "channel_id": again.meta["channel_id"]}
    assert again.timestamp == first.timestamp


def test_lost_insert_race_is_swallowed_and_keeps_winner_row(bot, monkeypatch):
    """Simulate another worker inserting the row after our existence check:
    the SELECT reports "missing", the INSERT then hits the composite PK.
    ``_ensure_end_user`` must not raise, and the values the other worker
    wrote are left as-is."""
    from peewee import IntegrityError

    from models.emly_users import EMLYUsers

    user_id = _uid()
    # The "other worker" wins the race.
    EMLYUsers.insert_new_user(
        bot_id=bot.id, id=user_id, first_name="Winner", last_name=None, email=None,
        phone=None, ip="", browser="google_chat", meta={"channel": "google_chat"},
    )

    # Our SELECT ran before their INSERT committed, so it saw nothing.
    real_get = EMLYUsers.get_user_by_id
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

    _ensure(bot.id, user_id, _channel(bot.id, "slack"),
            profile=UserProfile(first_name="Loser", email="l@example.com"))  # must not raise

    # The conflict path was really exercised (not short-circuited).
    assert len(raised) == 1 and isinstance(raised[0], IntegrityError)
    rows = _rows(bot.id, user_id)
    assert len(rows) == 1
    assert rows[0].first_name == "Winner"
    assert rows[0].email == "l@example.com", "gaps in the winner's row are still filled"
    assert rows[0].browser == "google_chat"
    monkeypatch.setattr(EMLYUsers, "get_user_by_id", real_get)


def test_unexpected_db_error_does_not_propagate(bot, monkeypatch):
    """Best-effort: any failure creating the user must not stop the reply."""
    from models.emly_users import EMLYUsers

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(EMLYUsers, "get_user_by_id", _boom)
    _ensure(bot.id, _uid(), _channel(bot.id))


def test_cache_failure_does_not_propagate(bot, monkeypatch):
    """The cache is part of the best-effort step too: an error in it is
    logged, not raised into the reply path."""
    from channels import dispatcher

    def _boom(*a, **kw):
        raise RuntimeError("cache broke")

    monkeypatch.setattr(dispatcher._KNOWN_END_USERS, "add", _boom)
    monkeypatch.setattr(type(dispatcher._KNOWN_END_USERS), "__contains__", _boom)
    _ensure(bot.id, _uid(), _channel(bot.id))


def test_known_sender_skips_the_database(bot, monkeypatch):
    """After the first message, later messages from the same sender in this
    process don't query for the user at all (it ran on every message)."""
    from models.emly_users import EMLYUsers

    channel = _channel(bot.id, "slack")
    user_id = _uid()
    adapter = _Adapter()
    _ensure(bot.id, user_id, channel, adapter=adapter)

    lookups: list = []
    monkeypatch.setattr(EMLYUsers, "get_user_by_id", lambda *a, **kw: lookups.append(a) or None)
    _ensure(bot.id, user_id, channel, adapter=adapter)
    assert lookups == []
    assert adapter.calls == ["U1"], "the platform is asked once per sender, not per message"


def test_failed_attempt_is_retried_on_the_next_message(bot, monkeypatch):
    from models.emly_users import EMLYUsers

    user_id = _uid()
    real_get = EMLYUsers.get_user_by_id

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(EMLYUsers, "get_user_by_id", _boom)
    _ensure(bot.id, user_id, _channel(bot.id))
    monkeypatch.setattr(EMLYUsers, "get_user_by_id", real_get)
    _ensure(bot.id, user_id, _channel(bot.id))
    assert EMLYUsers.get_user_by_id(bot.id, user_id) is not None


def test_known_sender_is_rechecked_after_the_ttl(bot, monkeypatch):
    """Data retention can delete the row; a returning sender gets it back."""
    from channels import dispatcher
    from models.emly_users import EMLYUser, EMLYUsers

    clock = [1000.0]
    monkeypatch.setattr(dispatcher.time, "monotonic", lambda: clock[0])

    channel = _channel(bot.id, "slack")
    user_id = _uid()
    _ensure(bot.id, user_id, channel)
    EMLYUser.delete().where((EMLYUser.bot == bot.id) & (EMLYUser.id == user_id)).execute()

    _ensure(bot.id, user_id, channel)
    assert EMLYUsers.get_user_by_id(bot.id, user_id) is None  # still cached

    clock[0] += 3601
    _ensure(bot.id, user_id, channel)
    assert EMLYUsers.get_user_by_id(bot.id, user_id) is not None


# ---------------------------------------------------------------------------
# What each adapter reads from its payload
# ---------------------------------------------------------------------------
def _request(body: dict):
    import json

    from starlette.requests import Request

    req = Request({"type": "http", "method": "POST", "headers": []})
    req._body = json.dumps(body).encode()
    return req


def test_google_chat_profile_from_sender():
    from channels.google_chat import GoogleChatAdapter, GoogleChatSecrets

    secrets = GoogleChatSecrets(service_account_json={}, service_account_email="bot@example.com")
    msg = {
        "sender": {"type": "HUMAN", "email": "ada@example.com", "displayName": "Ada King Lovelace"},
        "text": "hi", "space": {"name": "spaces/DM1", "type": "DM"},
    }
    inc = asyncio.run(GoogleChatAdapter().parse_inbound(_request({"type": "MESSAGE", "message": msg}), secrets))
    assert inc.user_profile == UserProfile(first_name="Ada", last_name="King Lovelace", email="ada@example.com")


def test_telegram_profile_from_sender():
    from channels.telegram import TelegramAdapter, TelegramSecrets

    secrets = TelegramSecrets(bot_token="1:x", bot_username="bot")
    body = {"message": {
        "message_id": 1, "text": "hi", "chat": {"id": 7, "type": "private"},
        "from": {"id": 7, "is_bot": False, "first_name": "Ada", "last_name": "Lovelace"},
    }}
    inc = asyncio.run(TelegramAdapter().parse_inbound(_request(body), secrets))
    assert (inc.user_profile.first_name, inc.user_profile.last_name) == ("Ada", "Lovelace")


def test_whatsapp_profile_from_contacts():
    from channels.whatsapp_cloud import WhatsAppCloudAdapter, WhatsAppCloudSecrets

    secrets = WhatsAppCloudSecrets.model_construct(phone_number_id="P1")
    body = {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "15550100", "profile": {"name": "Ada Lovelace"}}],
        "messages": [{"from": "15550100", "id": "m1", "type": "text", "text": {"body": "hi"}}],
    }}]}]}
    inc = asyncio.run(WhatsAppCloudAdapter().parse_inbound(_request(body), secrets))
    assert inc.user_profile == UserProfile(first_name="Ada", last_name="Lovelace", phone="+15550100")


def test_teams_profile_from_sender_and_group_chat_is_a_group():
    from channels.teams import TeamsAdapter, TeamsSecrets

    secrets = TeamsSecrets.model_construct(app_id="APP")
    body = {
        "type": "message", "id": "a1", "text": "hi", "serviceUrl": "https://smba.example",
        "from": {"id": "29:u", "aadObjectId": "aad-u", "name": "Ada Lovelace"},
        "conversation": {"id": "19:chat@thread.v2", "conversationType": "groupChat"},
        "entities": [{"type": "mention", "mentioned": {"id": "28:APP"}}],
    }
    inc = asyncio.run(TeamsAdapter().parse_inbound(_request(body), secrets))
    assert inc.user_profile.first_name == "Ada"
    assert inc.chat_type == "group"


def test_slack_profile_lookup_reads_users_info(monkeypatch):
    import httpx

    from channels import slack as slack_mod

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/users.info")
        assert request.url.params["user"] == "U1"
        return httpx.Response(200, json={"ok": True, "user": {"profile": {
            "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
        }}})

    monkeypatch.setattr(slack_mod, "make_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    adapter = slack_mod.SlackAdapter()

    async def _token(channel):
        return "xoxb-test"

    monkeypatch.setattr(adapter.auth, "get_access_token", _token)
    profile = asyncio.run(adapter.fetch_user_profile(_channel("b"), "U1"))
    assert profile == UserProfile(first_name="Ada", last_name="Lovelace", email="ada@example.com")


def test_slack_profile_lookup_without_scope_returns_nothing(monkeypatch):
    import httpx

    from channels import slack as slack_mod

    monkeypatch.setattr(slack_mod, "make_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"ok": False, "error": "missing_scope"})
    )))
    adapter = slack_mod.SlackAdapter()

    async def _token(channel):
        return "xoxb-test"

    monkeypatch.setattr(adapter.auth, "get_access_token", _token)
    assert asyncio.run(adapter.fetch_user_profile(_channel("b"), "U1")) is None
