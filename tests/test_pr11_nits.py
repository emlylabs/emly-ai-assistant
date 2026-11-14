"""Review nits on PR #11 that change what admins see:

- one "Website" bucket in analytics, covering widget chats stored before
  and after the bot got a web_widget install row (N2);
- sessions / messages from a deleted channel labelled "unknown", not
  "Website", and not taken over as if they were widget chats (N1);
- the bot's ``report_analysis_prompt`` actually drives the AI summary (N3);
- numeric strings judged by value in range rules, as the schema coerces
  them (N4).
"""
from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def bot(isolated_db):
    from models.bot_channels import BotChannel
    from models.bots import Bots
    from models.emly_messages import EMLYMessage
    from models.emly_sessions import EMLYSession

    bid = f"bot-{uuid.uuid4().hex[:8]}"
    b = Bots.insert(id=bid, slug=f"s-{bid}", name="B")
    yield b
    EMLYMessage.delete().where(EMLYMessage.bot == bid).execute()
    EMLYSession.delete().where(EMLYSession.bot_id == bid).execute()
    BotChannel.delete().where(BotChannel.bot == bid).execute()


@pytest.fixture
def client(isolated_db, monkeypatch):
    from models.admin_users import AdminUsers
    from routes import admin_bot_dashboard, admin_bot_sessions
    from services.auth.dependencies import get_admin

    stub = types.ModuleType("utils.dependencies")
    stub.AGENT_SERVICE_INSTANCE = types.SimpleNamespace(invalidate_bot=lambda _b: None)
    monkeypatch.setitem(sys.modules, "utils.dependencies", stub)
    app = FastAPI()
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.include_router(admin_bot_sessions.router, prefix="/api/admin")
    admin = AdminUsers.insert(
        id=f"adm-{uuid.uuid4().hex[:6]}", email="super@example.com", is_active=True, is_superadmin=True,
    )
    app.dependency_overrides[get_admin] = lambda: admin
    return TestClient(app)


NOW = datetime.utcnow()


def _channel(bot_id, type_):
    from models.bot_channels import BotChannel

    cid = f"ch-{uuid.uuid4().hex[:8]}"
    BotChannel.create(id=cid, bot=bot_id, type=type_, created_at=NOW)
    return cid


def _message(bot_id, channel_id, session_id="s"):
    from models.emly_messages import EMLYMessage

    EMLYMessage.create(
        bot=bot_id, user_id="u", session_id=session_id, message="hi", role="user",
        created_at=NOW, updated_at=NOW, channel_id=channel_id,
    )


def _session(bot_id, sid, channel_id):
    from models.emly_sessions import EMLYSession

    EMLYSession.create(
        id=sid, bot_id=bot_id, user_id="u", channel_id=channel_id,
        started_at=NOW, last_activity_at=NOW, turn_count=1,
    )


def _by_channel(client, bot):
    window = {"from": int((NOW - timedelta(hours=1)).timestamp()), "to": int((NOW + timedelta(hours=1)).timestamp())}
    r = client.get(f"/api/admin/bots/{bot.slug}/messages/by-channel", params=window)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------- N2
def test_website_is_one_bucket_before_and_after_widget_install(client, bot):
    _message(bot.id, None)  # before the web_widget row existed
    widget = _channel(bot.id, "web_widget")
    _message(bot.id, widget)
    _message(bot.id, widget)
    slack = _channel(bot.id, "slack")
    _message(bot.id, slack)

    rows = _by_channel(client, bot)
    website = [r for r in rows if r["channel_type"] == "web_widget"]
    assert website == [{"channel_id": "web_widget", "channel_type": "web_widget", "display_name": None, "count": 3}]
    assert {"channel_id": slack, "count": 1}.items() <= next(r for r in rows if r["channel_id"] == slack).items()


def test_website_filter_matches_the_merged_bucket(bot):
    from models.bot_channels import DEFAULT_WIDGET_CHANNEL_ID
    from models.emly_sessions import EMLYSessions

    widget = _channel(bot.id, "web_widget")
    slack = _channel(bot.id, "slack")
    _session(bot.id, "old-widget", None)
    _session(bot.id, "new-widget", widget)
    _session(bot.id, "slack", slack)

    ids = {s.id for s in EMLYSessions.list_for_bot(bot.id, channel_id=DEFAULT_WIDGET_CHANNEL_ID)}
    assert ids == {"old-widget", "new-widget"}
    assert EMLYSessions.count_for_bot(bot.id, channel_id=DEFAULT_WIDGET_CHANNEL_ID) == 2
    # Filtering by the install row's own id still narrows to just that row.
    assert {s.id for s in EMLYSessions.list_for_bot(bot.id, channel_id=widget)} == {"new-widget"}


# --------------------------------------------------------------------- N1
def test_deleted_channel_is_unknown_not_website(client, bot):
    _message(bot.id, "ch-gone")
    _session(bot.id, "orphan", "ch-gone")

    rows = _by_channel(client, bot)
    assert [r for r in rows if r["channel_id"] == "ch-gone"] == [
        {"channel_id": "ch-gone", "channel_type": "unknown", "display_name": None, "count": 1}
    ]
    listed = client.get(f"/api/admin/bots/{bot.slug}/sessions").json()["items"]
    assert [i["channel_type"] for i in listed if i["session"]["id"] == "orphan"] == ["unknown"]
    detail = client.get(f"/api/admin/bots/{bot.slug}/sessions/orphan").json()
    assert detail["channel_type"] == "unknown"


def test_deleted_channel_session_cannot_be_taken_over(client, bot):
    _session(bot.id, "orphan", "ch-gone")
    r = client.post(f"/api/admin/bots/{bot.slug}/sessions/orphan/takeover")
    assert r.status_code == 400
    assert r.json()["detail"]["channel"] == "unknown"


# --------------------------------------------------------------------- N3
def test_summary_prompt_precedence(bot):
    from config import SUMMARY_RECOMMENDATION_PROMPT
    from services.bot_config import save_config_for_bot
    from services.widget_email import _summary_instructions

    assert _summary_instructions(bot.id, "") == SUMMARY_RECOMMENDATION_PROMPT

    save_config_for_bot(bot.id, {"report_analysis_prompt": "  Bot default.  "})
    assert _summary_instructions(bot.id, "") == "Bot default."
    assert _summary_instructions(bot.id, "   ") == "Bot default."
    assert _summary_instructions(bot.id, "Form prompt.") == "Form prompt."


def test_summary_prompt_survives_config_errors():
    from config import SUMMARY_RECOMMENDATION_PROMPT
    from services.widget_email import _summary_instructions

    assert _summary_instructions("no-such-bot", "") == SUMMARY_RECOMMENDATION_PROMPT
    assert _summary_instructions("", "") == SUMMARY_RECOMMENDATION_PROMPT


# --------------------------------------------------------------------- N4
@pytest.mark.parametrize("config,key,bad", [
    ({"llm": {"temperature": "0.7"}}, "R20_temperature_range", False),
    ({"llm": {"temperature": " 1 "}}, "R20_temperature_range", False),
    ({"llm": {"temperature": "3"}}, "R20_temperature_range", True),
    ({"llm": {"temperature": "warm"}}, "R20_temperature_range", True),
    ({"llm": {"temperature": "nan"}}, "R20_temperature_range", True),
    ({"rag": {"top_k": "5"}}, "R21_top_k_range", False),
    ({"rag": {"embedding_threshold": "0.25"}}, "R22_threshold_range", False),
    ({"limits": {"max_file_size_mb": "0"}}, "R23_max_file_size_range", True),
])
def test_numeric_strings_are_judged_by_value(config, key, bad):
    from services.config_validation import validate_config

    keys = {i.key for i in validate_config(config) if i.severity == "error"}
    assert (key in keys) is bad
