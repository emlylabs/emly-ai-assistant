"""G1b: submitted form values never reach an LLM call.

A form submission is stored as a ``call_back`` user message holding every
field the visitor typed, so admins see it in the transcript. It used to
flow into the router's conversation history, the next form alert's AI
summary and the enrichment classifier.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from services import widget_email

SECRET = "passport X1234567"


def _uid(p):
    return f"{p}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def scene(isolated_db, monkeypatch):
    """A bot, a visitor whose stored form details hold ``SECRET``, and one
    ordinary chat turn. Emails go nowhere."""
    from models.bots import Bots
    from models.emly_messages import EMLYMessages
    from models.emly_users import EMLYUsers

    class FakeSender:
        def __init__(self, **kw):
            pass

        def send_email(self, msg):
            return True

    monkeypatch.setattr(widget_email, "EmailSender", FakeSender)
    # "full" alerts are the ones that run an AI summary.
    bot = Bots.insert(id=_uid("bot"), slug=_uid("ff"), name="Acme", config_json={"alert_email_content": "full"})
    EMLYUsers.insert_new_user(bot_id=bot.id, id="v1", first_name="Ann", last_name=None,
                              email="ann@example.com", phone="", ip="", browser="",
                              meta={"document": SECRET})
    session = _uid("sess")
    EMLYMessages.insert_new_message(bot_id=bot.id, user_id="v1", session_id=session, role="user",
                                    message="how do I renew?", not_useful=False,
                                    expanded_query=None, page="p")
    return bot, session


def _submit_form(bot, session):
    widget_email.send_scheduled_email("alerts@example.com", "v1", {"session_id": session},
                                      "Renewal", [], "p", "", bot.id)


def test_submission_is_still_in_the_transcript(scene):
    from models.emly_messages import EMLYMessages

    bot, session = scene
    _submit_form(bot, session)
    rows = EMLYMessages.list_all(bot.id, session_id=session)
    assert any(SECRET in r.message for r in rows)


def test_router_history_leaves_out_the_submission(scene):
    from agents.conversation_agent import ConversationManager

    bot, session = scene
    _submit_form(bot, session)

    agent = ConversationManager.__new__(ConversationManager)
    agent.bot_id, agent.user_id, agent.session_id, agent.window_size = bot.id, "v1", session, 5
    agent.global_memory = SimpleNamespace(chat_memory=SimpleNamespace(messages=[]))
    agent.load_global_memory_with_messages()

    history = [m.content for m in agent.global_memory.chat_memory.messages]
    assert history == ["how do I renew?"]


def test_skipped_rows_dont_use_up_the_history_window(scene):
    from models.emly_messages import EMLYMessages

    bot, session = scene
    for _ in range(3):
        _submit_form(bot, session)
    (turn,) = EMLYMessages.get_messages(bot.id, "v1", session, limit=1)
    assert turn.message == "how do I renew?"


def test_next_alert_summary_leaves_out_the_earlier_submission(scene, monkeypatch):
    bot, session = scene
    seen = []
    monkeypatch.setattr(widget_email, "_summarize_conversation",
                        lambda bot_id, messages, prompt: seen.append(messages) or "summary")

    _submit_form(bot, session)
    _submit_form(bot, session)

    second = "\n".join(m.message for m in seen[1])
    assert "how do I renew?" in second
    assert SECRET not in second
    assert all(m.expanded_query != "call_back" for m in seen[1])


def test_submission_is_not_sent_to_enrichment(scene, monkeypatch):
    from services import enrichment

    bot, session = scene
    queued = []
    monkeypatch.setattr(enrichment, "enqueue", lambda **kw: queued.append(kw["user_text"]) or True)

    _submit_form(bot, session)
    assert queued == []

    from models.emly_messages import EMLYMessages
    EMLYMessages.insert_new_message(bot_id=bot.id, user_id="v1", session_id=session, role="user",
                                    message="thanks", not_useful=False, expanded_query=None, page="p")
    assert queued == ["thanks"]
