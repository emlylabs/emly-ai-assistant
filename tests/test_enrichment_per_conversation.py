"""Q-G1: sentiment and intent are classified once per finished conversation.

Every visitor message used to be sent to the classifier as it arrived
(one model call per message) while the UI said one per conversation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def enrichment(isolated_db):
    from services import enrichment as mod

    while not mod._QUEUE.empty():
        mod._QUEUE.get_nowait()
        mod._QUEUE.task_done()
    mod._PENDING.clear()
    yield mod
    while not mod._QUEUE.empty():
        mod._QUEUE.get_nowait()
        mod._QUEUE.task_done()
    mod._PENDING.clear()


def _bot(enabled=True):
    from models.bots import Bots

    return Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"en-{uuid.uuid4().hex[:6]}", name="E",
                       config_json={"enrichment_enabled": enabled})


def _conversation(bot_id, *, quiet_minutes, messages=("my payslip is wrong", "it's missing overtime"),
                  enriched_minutes_ago=None):
    from models.emly_messages import EMLYMessages
    from models.emly_sessions import EMLYSession

    sid = f"s-{uuid.uuid4().hex[:8]}"
    for text in messages:
        EMLYMessages.insert_new_message(bot_id=bot_id, user_id="u", session_id=sid, role="user", message=text,
                                        not_useful=False, expanded_query=None, page="p")
        EMLYMessages.insert_new_message(bot_id=bot_id, user_id="u", session_id=sid, role="assistant",
                                        message="ANSWER-TEXT", not_useful=False, expanded_query=None, page="p")
    updates = {"last_activity_at": _now() - timedelta(minutes=quiet_minutes)}
    if enriched_minutes_ago is not None:
        updates["enrichment_at"] = _now() - timedelta(minutes=enriched_minutes_ago)
    EMLYSession.update(**updates).where(EMLYSession.id == sid).execute()
    return sid


def _queued(mod):
    return {job.session_id for job in list(mod._QUEUE.queue)}


def _drain(mod):
    """Empty the queue (other tests' conversations may be in it too)."""
    jobs = []
    while not mod._QUEUE.empty():
        jobs.append(mod._QUEUE.get_nowait())
        mod._QUEUE.task_done()
    mod._PENDING.clear()
    return jobs


def _take(mod, sid):
    (job,) = [j for j in _drain(mod) if j.session_id == sid]
    return job


def test_messages_no_longer_queue_anything(enrichment):
    bot = _bot()
    _conversation(bot.id, quiet_minutes=0)
    assert enrichment.queue_depth() == 0


def test_sweep_queues_only_finished_unclassified_conversations(enrichment):
    bot = _bot()
    done = _conversation(bot.id, quiet_minutes=45)
    still_going = _conversation(bot.id, quiet_minutes=5)
    classified = _conversation(bot.id, quiet_minutes=45, enriched_minutes_ago=10)
    resumed = _conversation(bot.id, quiet_minutes=45, enriched_minutes_ago=120)
    off_bot = _bot(enabled=False)
    other = _conversation(off_bot.id, quiet_minutes=45)

    enrichment.sweep_finished()
    queued = _queued(enrichment)
    assert done in queued and resumed in queued
    assert not queued & {still_going, classified, other}

    enrichment.sweep_finished()  # already queued: not queued twice
    assert enrichment.queue_depth() == len(queued)


def test_one_call_per_conversation_with_the_visitor_side_only(enrichment, monkeypatch):
    from models.emly_sessions import EMLYSession

    bot = _bot()
    sid = _conversation(bot.id, quiet_minutes=45)
    prompts = []

    class _LLM:
        def invoke(self, prompt):
            prompts.append(prompt)
            return type("R", (), {"content": '{"sentiment_label":"negative","sentiment_score":-0.5,'
                                              '"intent":"payroll_issue","intent_confidence":0.8}'})()

    monkeypatch.setattr(enrichment, "_build_llm_for_bot", lambda _b: _LLM())
    enrichment.sweep_finished()
    job = _take(enrichment, sid)
    enrichment._apply_result(job, enrichment._classify_one(job) or {})

    (prompt,) = prompts
    assert "my payslip is wrong" in prompt and "it's missing overtime" in prompt
    assert prompt.index("my payslip") < prompt.index("missing overtime")
    assert "ANSWER-TEXT" not in prompt
    row = EMLYSession.get(EMLYSession.id == sid)
    assert row.intent == "payroll_issue" and row.enrichment_at is not None

    enrichment.sweep_finished()
    assert sid not in _queued(enrichment)  # classified: not sent again


def test_failed_classification_is_not_retried_every_sweep(enrichment, monkeypatch):
    bot = _bot()
    sid = _conversation(bot.id, quiet_minutes=45)
    monkeypatch.setattr(enrichment, "_build_llm_for_bot", lambda _b: None)
    enrichment.sweep_finished()
    job = _take(enrichment, sid)
    enrichment._apply_result(job, enrichment._classify_one(job) or {})
    enrichment.sweep_finished()
    assert sid not in _queued(enrichment)


def test_input_is_capped_to_the_latest_text(enrichment):
    bot = _bot()
    sid = _conversation(bot.id, quiet_minutes=45, messages=("OLDEST " + "x" * 5000, "NEWEST"))
    text = enrichment.conversation_text(bot.id, sid)
    assert len(text) == enrichment.MAX_CHARS and text.endswith("NEWEST") and "OLDEST" not in text
