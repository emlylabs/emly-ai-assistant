"""Q-G12: "Answer only from your documents" (``require_citation``).

Topics that don't search documents answered from the model's general
knowledge, with no source. With the setting on, a direct-answer topic
searches the bot's documents anyway: a match is answered from them with
citations; no match gets the "cannot find" reply and no model call.
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from agents.conversation_agent import CONTEXT_START, TopicAgent
from config import DEFAULT_MESSAGE

HIT = {"metadata": {"filename": "policy.pdf", "relevance_score": 0.9}, "chunk": "Leave is 20 days."}


class _Memory:
    def load_memory_variables(self, _):
        return {"history": []}


def _agent(*, require, found, sent):
    agent = TopicAgent.__new__(TopicAgent)
    agent.topic_name = "chat"
    agent.logger = logging.getLogger("t")
    agent.llm = RunnableLambda(lambda pv: sent.append(pv.to_string()) or AIMessage("answer"))
    agent.rag_manager = SimpleNamespace(
        should_use_rag=lambda topic: False,  # a topic not set to search documents
        get_context=lambda q, topic: ("", []),
        search=lambda q: ("Leave is 20 days.", [HIT]) if found else ("", []),
    )
    agent.prompt_manager = SimpleNamespace(get_prompt=lambda name, topic: "{context}\n{user_input}")
    agent.conversation_manager = SimpleNamespace(bot_id="b")
    agent._require_citation = lambda: require
    return agent


def _direct(agent, stream=False):
    state = agent._handle_direct_response({"stream": stream}, "how much leave?", _Memory())
    response = state["response"]
    return ("".join(response) if stream else response), state["citations"]


def test_off_answers_from_the_model_without_searching():
    sent = []
    text, citations = _direct(_agent(require=False, found=True, sent=sent))
    assert text == "answer" and citations == []
    assert CONTEXT_START not in sent[0]


@pytest.mark.parametrize("stream", [False, True])
def test_on_answers_from_documents_with_citations(stream):
    sent = []
    text, citations = _direct(_agent(require=True, found=True, sent=sent), stream=stream)
    assert text == "answer" and citations
    assert CONTEXT_START in sent[0] and "Leave is 20 days." in sent[0]


@pytest.mark.parametrize("stream", [False, True])
def test_on_with_no_match_says_it_cannot_find_and_skips_the_model(stream):
    sent = []
    text, citations = _direct(_agent(require=True, found=False, sent=sent), stream=stream)
    assert text == DEFAULT_MESSAGE and citations == []
    assert sent == []


# ------------------------------------------------------------ new-bot default
@pytest.fixture
def client(isolated_db):
    from models.admin_users import AdminUsers
    from routes import admin_bots
    from services.auth.dependencies import get_admin

    app = FastAPI()
    app.include_router(admin_bots.router, prefix="/api/admin")
    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="rc@example.com", is_active=True,
                              is_superadmin=True)
    app.dependency_overrides[get_admin] = lambda: admin
    return TestClient(app)


def _create(client, **extra):
    from models.bots import Bots
    from services.bot_config import get_config_for_bot

    slug = f"rc-{uuid.uuid4().hex[:6]}"
    assert client.post("/api/admin/bots", json={"slug": slug, "name": "R", **extra}).status_code == 201
    return get_config_for_bot(Bots.get_by_slug(slug).id).require_citation


def test_new_bots_start_on_existing_and_duplicates_unchanged(client):
    from models.bots import Bots
    from services.bot_config import get_config_for_bot

    assert _create(client) is True
    assert _create(client, template_id="lead-capture") is True
    old = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"o-{uuid.uuid4().hex[:6]}", name="O",
                      config_json={})
    assert get_config_for_bot(old.id).require_citation is False
    assert _create(client, source_bot_id=old.id) is False
