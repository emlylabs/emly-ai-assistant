"""G6a: REQUIRE_PER_BOT_LLM_KEY stops bots using the deployment key.

A bot without its own API key silently used OPENAI_API_KEY, so one
department's traffic and spend went through another's key. With the
flag on, such a bot refuses to answer, and the admin sees why.
"""
from __future__ import annotations

import uuid

import pytest

from services import llm_settings


@pytest.fixture
def deployment(monkeypatch):
    monkeypatch.setenv("MODEL", "dep-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.internal/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "dep-key")
    monkeypatch.delenv(llm_settings.REQUIRE_BOT_KEY_ENV, raising=False)
    monkeypatch.delenv(llm_settings.ALLOWLIST_ENV, raising=False)
    return monkeypatch


def _bot(api_key=None):
    from models.bots import Bots
    from services.bot_config import set_api_key

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"k-{uuid.uuid4().hex[:6]}", name="K",
                      config_json={"llm": {}})
    if api_key:
        set_api_key(bot.id, api_key)
    return bot


def test_default_still_falls_back(isolated_db, deployment):
    assert llm_settings.require(_bot().id).api_key == "dep-key"


def test_flag_on_a_bot_without_a_key_refuses(isolated_db, deployment):
    deployment.setenv(llm_settings.REQUIRE_BOT_KEY_ENV, "true")
    bot = _bot()
    assert llm_settings.resolve(bot.id).missing == ["api_key"]
    with pytest.raises(llm_settings.LLMNotConfigured):
        llm_settings.require(bot.id)


def test_flag_on_a_bot_with_its_own_key_answers(isolated_db, deployment):
    deployment.setenv(llm_settings.REQUIRE_BOT_KEY_ENV, "true")
    assert llm_settings.require(_bot(api_key="bot-key").id).api_key == "bot-key"


@pytest.mark.parametrize("stream", [False, True])
def test_flag_on_visitor_gets_the_not_available_reply(isolated_db, deployment, monkeypatch, stream):
    from agents.conversation_agent import ConversationSessionManager
    from services.agent_service import AgentService

    deployment.setenv(llm_settings.REQUIRE_BOT_KEY_ENV, "true")
    built = []
    monkeypatch.setattr("agents.conversation_agent.ChatOpenAI", lambda **kw: built.append(kw) or object())
    svc = AgentService()
    svc.session_manager = ConversationSessionManager()
    out = svc.process_message(bot_id=_bot().id, user_id="u", session_id="s", page_id="p",
                              message="hi", stream=stream)
    first = next(iter(out))["data"] if stream else out[0]
    assert first == llm_settings.NOT_CONFIGURED_REPLY
    assert built == []  # the deployment key was never handed to a client


def test_flag_on_other_bot_calls_skip_too(isolated_db, deployment):
    """Enrichment and alert summaries resolve the same way."""
    from services import enrichment

    deployment.setenv(llm_settings.REQUIRE_BOT_KEY_ENV, "true")
    bot = _bot()
    enrichment._LLM_CACHE.pop(bot.id, None)
    assert enrichment._build_llm_for_bot(bot.id) is None


def test_admin_sees_why(isolated_db, deployment):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from models.admin_users import AdminUsers
    from routes import admin_bot_dashboard
    from services.auth.dependencies import get_admin
    from services.config_validation import ValidationContext, has_deployment_api_key, validate_config

    deployment.setenv(llm_settings.REQUIRE_BOT_KEY_ENV, "true")
    assert has_deployment_api_key() is False  # the deployment key isn't a fallback for bots
    (issue,) = [i for i in validate_config({"llm": {}}, ValidationContext(
        has_bot_api_key=False, has_deployment_api_key=has_deployment_api_key())) if i.key == "RT1_missing_api_key"]
    assert llm_settings.REQUIRE_BOT_KEY_ENV in issue.message

    bot = _bot()
    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="sa@example.com", is_active=True,
                              is_superadmin=True)
    app = FastAPI()
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: admin
    body = TestClient(app).get(f"/api/admin/bots/{bot.slug}/api-key/status").json()
    assert body["has_deployment_fallback"] is False and body["missing"] == ["api_key"]
