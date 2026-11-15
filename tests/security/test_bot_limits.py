"""G13a: a bot's cost limits are enforced before any model call.

``daily_token_cap``, ``messages_per_minute_per_user`` and
``messages_per_minute_per_bot`` were stored and validated but nothing
enforced them. They now apply on the one path the widget and every chat
channel use (``AgentService.process_message``).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from services import bot_limits


def _bot(limits):
    from models.bots import Bots

    return Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"lim-{uuid.uuid4().hex[:6]}", name="L",
                       config_json={"limits": limits})


@pytest.fixture
def agent(isolated_db):
    """AgentService whose handler answers without a model and counts calls."""
    from services.agent_service import AgentService

    answered = []
    handler = SimpleNamespace(topics={"t": object()},
                              process_user_input=lambda msg, *a, **k: answered.append(msg) or ("answer", []))
    svc = AgentService()
    svc.session_manager = SimpleNamespace(get_handler=lambda *a: handler)
    bot_limits._token_totals.clear()
    yield svc, answered
    bot_limits._token_totals.clear()


def _send(svc, bot, user="u1", stream=False):
    return svc.process_message(bot_id=bot.id, user_id=user, session_id=f"s-{user}", page_id="p",
                               message="hi", stream=stream)


def test_per_user_limit(agent):
    svc, answered = agent
    bot = _bot({"messages_per_minute_per_user": 2})
    assert [_send(svc, bot)[0] for _ in range(3)] == ["answer", "answer", bot_limits.BUSY_REPLY]
    assert _send(svc, bot, user="u2")[0] == "answer"  # other visitors unaffected
    assert len(answered) == 3


def test_per_bot_limit(agent):
    svc, answered = agent
    bot = _bot({"messages_per_minute_per_bot": 2})
    replies = [_send(svc, bot, user=f"u{i}")[0] for i in range(3)]
    assert replies == ["answer", "answer", bot_limits.BUSY_REPLY]
    other = _bot({"messages_per_minute_per_bot": 2})
    assert _send(svc, other)[0] == "answer"


def test_daily_token_cap(agent):
    from models.emly_messages import EMLYMessages

    svc, answered = agent
    bot = _bot({"daily_token_cap": 100})
    assert _send(svc, bot)[0] == "answer"
    EMLYMessages.insert_new_message(bot_id=bot.id, user_id="u1", session_id="s", role="assistant",
                                    message="a", not_useful=False, expanded_query=None, page="p",
                                    prompt_tokens=80, completion_tokens=20)
    bot_limits._token_totals.clear()  # skip the cache window

    assert _send(svc, bot)[0] == bot_limits.DAILY_CAP_REPLY
    events = list(_send(svc, bot, stream=True))
    assert events[0] == {"type": "token", "data": bot_limits.DAILY_CAP_REPLY}
    assert answered == ["hi"]


def test_old_tokens_dont_count(agent):
    from datetime import datetime, timedelta

    from models.emly_messages import EMLYMessage

    svc, _ = agent
    bot = _bot({"daily_token_cap": 100})
    EMLYMessage.create(bot=bot.id, user_id="u", session_id="s", role="assistant", message="a",
                       created_at=datetime.utcnow() - timedelta(hours=25), updated_at=datetime.utcnow(),
                       prompt_tokens=500, completion_tokens=500)
    assert bot_limits.tokens_used_last_24h(bot.id) == 0
    assert _send(svc, bot)[0] == "answer"


@pytest.mark.parametrize("limits", [{}, {"daily_token_cap": 0, "messages_per_minute_per_user": 0,
                                          "messages_per_minute_per_bot": None}])
def test_empty_or_zero_means_no_limit(agent, limits):
    svc, answered = agent
    bot = _bot(limits)
    for _ in range(30):
        assert _send(svc, bot)[0] == "answer"
