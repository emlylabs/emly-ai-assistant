"""Out-of-scope questions must not open the lead form (NEW-03)."""
from __future__ import annotations

from types import SimpleNamespace

from agents.intent_routing import (
    collects_contact_details,
    prefer_answer_topic,
    wants_to_be_contacted,
)


def _topic(name, *, slots=True, skip=False):
    slot = SimpleNamespace(name="full_name", required=True)
    return SimpleNamespace(name=name, skip_slot_filling=skip, slots=[slot] if slots else [])


def test_product_question_is_not_a_contact_request():
    assert wants_to_be_contacted("Do you sell air conditioners too?") is False
    assert wants_to_be_contacted("What is the Home plan price?") is False


def test_quote_and_callback_are_contact_requests():
    assert wants_to_be_contacted("I'd like a personalised quote") is True
    assert wants_to_be_contacted("Can you call me tomorrow?") is True
    assert wants_to_be_contacted("Please contact me") is True


def test_answering_topic_wins_over_lead_capture():
    topics = {
        "lead_capture": _topic("lead_capture"),
        "sales": _topic("sales", slots=False, skip=True),
    }
    chosen = prefer_answer_topic(
        "lead_capture",
        "Do you sell air conditioners too?",
        topics,
        ["lead_capture", "sales"],
    )
    assert chosen == "sales"


def test_quote_stays_on_the_contact_topic():
    topics = {
        "lead_capture": _topic("lead_capture"),
        "sales": _topic("sales", slots=False, skip=True),
    }
    assert prefer_answer_topic(
        "lead_capture", "Can I get a quote?", topics, ["lead_capture", "sales"]
    ) == "lead_capture"


def test_only_contact_topic_is_left_for_the_agent_to_decline():
    topics = {"lead_capture": _topic("lead_capture")}
    assert prefer_answer_topic(
        "lead_capture", "Do you sell air conditioners too?", topics, ["lead_capture"]
    ) == "lead_capture"
    assert collects_contact_details(topics["lead_capture"]) is True
    assert collects_contact_details(_topic("sales", skip=True)) is False
