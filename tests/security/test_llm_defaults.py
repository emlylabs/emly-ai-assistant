"""D1: no built-in model or endpoint; an unconfigured bot refuses to answer."""

from __future__ import annotations

import uuid

import pytest

from services import llm_settings

LLM_ENVS = (llm_settings.MODEL_ENV, llm_settings.ENDPOINT_ENV, llm_settings.API_KEY_ENV)


@pytest.fixture
def no_deployment_llm(monkeypatch):
    for name in LLM_ENVS:
        monkeypatch.delenv(name, raising=False)


def _bot(llm=None, api_key=None):
    from models.bots import Bots
    from services.bot_config import set_api_key

    config = {
        "schema_version": 1,
        "topics": {
            "support": {
                "name": "support", "description": "Answer questions.", "requires_rag": False,
                "skip_slot_filling": True, "slots": [],
                "prompts": {"llm_response": "Answer: {user_input}"},
            },
        },
        "llm": {"model_type": "openai", **(llm or {})},
    }
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"l-{uuid.uuid4().hex[:6]}", name="L",
                      config_json=config)
    if api_key:
        set_api_key(bot.id, api_key)
    return bot


# ------------------------------------------------------------- resolution


def test_nothing_configured_means_everything_missing(isolated_db, no_deployment_llm):
    bot = _bot()
    assert llm_settings.resolve(bot.id).missing == ["model", "endpoint", "api_key"]
    with pytest.raises(llm_settings.LLMNotConfigured) as exc:
        llm_settings.require(bot.id)
    assert exc.value.missing == ["model", "endpoint", "api_key"]


def test_deployment_defaults_fill_gaps(isolated_db, monkeypatch):
    monkeypatch.setenv("MODEL", "dep-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.internal/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "dep-key")
    s = llm_settings.require(_bot().id)
    assert (s.model, s.endpoint, s.api_key) == ("dep-model", "https://llm.internal/v1", "dep-key")


def test_bot_settings_win_over_deployment(isolated_db, monkeypatch):
    monkeypatch.setenv("MODEL", "dep-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.internal/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "dep-key")
    bot = _bot({"model": "bot-model", "api_endpoint": "https://bot.example/v1"}, api_key="bot-key")
    s = llm_settings.require(bot.id)
    assert (s.model, s.endpoint, s.api_key) == ("bot-model", "https://bot.example/v1", "bot-key")
    assert "bot-key" not in repr(s)


def test_blank_values_count_as_missing(isolated_db, monkeypatch):
    monkeypatch.setenv("MODEL", "   ")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    bot = _bot({"model": "", "api_endpoint": "  "})
    assert llm_settings.resolve(bot.id).missing == ["model", "endpoint"]


def test_templates_ship_no_model_or_endpoint():
    from services.bot_templates import _TEMPLATES

    for template_id, template in _TEMPLATES.items():
        llm = template.build_config("slug", "Name")["llm"]
        assert "model" not in llm and "api_endpoint" not in llm, template_id


def test_config_module_has_no_provider_defaults():
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[2] / "config.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "api.openai.com" not in node.value
            assert ":free" not in node.value


# ---------------------------------------------------------------- refusal


@pytest.fixture
def agent(monkeypatch):
    from agents.conversation_agent import ConversationSessionManager
    from services.agent_service import AgentService

    built = []
    monkeypatch.setattr("agents.conversation_agent.ChatOpenAI", lambda **kw: built.append(kw) or object())
    svc = AgentService()
    svc.session_manager = ConversationSessionManager()
    svc.built = built
    return svc


@pytest.mark.parametrize("stream", [False, True])
def test_unconfigured_bot_refuses_with_safe_reply(isolated_db, no_deployment_llm, agent, stream, caplog):
    bot = _bot({"model": "m"}, api_key="secret-key")  # endpoint missing
    out = agent.process_message(bot_id=bot.id, user_id="u1", session_id="s1", page_id="p",
                                message="hello", stream=stream)
    if stream:
        events = list(out)
        assert events[0] == {"type": "token", "data": llm_settings.NOT_CONFIGURED_REPLY}
        assert events[-1]["type"] == "citations"
    else:
        assert out == (llm_settings.NOT_CONFIGURED_REPLY, [])
    assert agent.built == []  # never reached a provider client
    assert "missing endpoint" in caplog.text
    assert "secret-key" not in caplog.text


def test_configured_bot_builds_client_with_resolved_settings(isolated_db, no_deployment_llm, agent):
    bot = _bot({"model": "m", "api_endpoint": "https://bot.example/v1"}, api_key="k")
    agent.session_manager._build_runtime(bot.id)
    assert len(agent.built) == 1
    kw = agent.built[0]
    assert (kw["model"], kw["base_url"], kw["api_key"]) == ("m", "https://bot.example/v1", "k")


def test_enrichment_skips_unconfigured_bot(isolated_db, no_deployment_llm):
    from services import enrichment

    bot = _bot()
    enrichment._LLM_CACHE.pop(bot.id, None)
    assert enrichment._build_llm_for_bot(bot.id) is None
    assert bot.id not in enrichment._LLM_CACHE


# ------------------------------------------------------------- admin view


def _ctx(**kw):
    from services.config_validation import ValidationContext

    return ValidationContext(has_bot_api_key=True, has_deployment_api_key=False, **kw)


def _keys(config, ctx):
    from services.config_validation import validate_config

    return {i.key for i in validate_config(config, ctx) if i.severity == "error"}


def test_validation_flags_missing_model_and_endpoint():
    no_defaults = _ctx(has_deployment_model=False, has_deployment_endpoint=False)
    assert {"RT4_missing_model", "RT5_missing_endpoint"} <= _keys({"llm": {}}, no_defaults)
    assert not {"RT4_missing_model", "RT5_missing_endpoint"} & _keys(
        {"llm": {"model": "m", "api_endpoint": "https://e/v1"}}, no_defaults)
    with_defaults = _ctx(has_deployment_model=True, has_deployment_endpoint=True)
    assert not {"RT4_missing_model", "RT5_missing_endpoint"} & _keys({"llm": {}}, with_defaults)
    # Unprobed context: rules stay quiet rather than guess.
    assert not {"RT4_missing_model", "RT5_missing_endpoint"} & _keys({"llm": {}}, _ctx())


def test_api_key_status_reports_missing(isolated_db, no_deployment_llm):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from models.admin_users import AdminUsers
    from routes import admin_bot_dashboard
    from services.auth.dependencies import get_admin

    bot = _bot({"model": "m"})
    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="sa@example.com",
                              is_active=True, is_superadmin=True)
    app = FastAPI()
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: admin
    body = TestClient(app).get(f"/api/admin/bots/{bot.slug}/api-key/status").json()
    assert body["missing"] == ["endpoint", "api_key"]
    assert body["has_deployment_model"] is False and body["has_deployment_endpoint"] is False
