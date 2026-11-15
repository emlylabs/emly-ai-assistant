"""G2a: LLM_ENDPOINT_ALLOWLIST limits where any bot may send model traffic.

Any bot owner could point a bot at any URL; R19 only checked it was
http(s). With the allowlist set, an off-list endpoint can't be saved (on
the config editor or any creation path) and a bot already holding one
refuses to answer instead of sending conversations there.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from services import llm_settings
from services.config_validation import ValidationContext, blocking_format_issues, validate_config

ALLOW = "https://llm.internal/v1, models.example.com, *.openai.azure.com, gpu-box:8443"


@pytest.fixture
def allowlist(monkeypatch):
    monkeypatch.setenv("LLM_ENDPOINT_ALLOWLIST", ALLOW)
    for name in ("MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def no_allowlist(monkeypatch):
    monkeypatch.delenv("LLM_ENDPOINT_ALLOWLIST", raising=False)


@pytest.mark.parametrize("endpoint, allowed", [
    ("https://llm.internal/v1", True),
    ("https://llm.internal/v1/", True),
    ("https://LLM.internal:443/v1/chat", True),
    ("https://llm.internal/v10", False),         # path prefix stops at a segment
    ("https://llm.internal/", False),
    ("http://llm.internal/v1", False),           # scheme is part of a URL entry
    ("https://llm.internal:8443/v1", False),     # so is the port
    ("https://models.example.com/anything", True),
    ("http://models.example.com:9000/v1", True),  # host entry: any scheme, port, path
    ("https://evil.models.example.com/v1", False),
    ("https://my-res.openai.azure.com/openai", True),
    ("https://openai.azure.com/", False),        # wildcard needs a subdomain
    ("https://my-res.openai.azure.com.evil.io/", False),
    ("http://gpu-box:8443/v1", True),
    ("http://gpu-box/v1", False),
    ("https://models.example.com@evil.io/v1", False),  # userinfo trick: the host is evil.io
    ("https://api.openai.com/v1", False),
    ("not a url", False),
    ("ftp://models.example.com/", False),
])
def test_matching(allowlist, endpoint, allowed):
    assert llm_settings.is_endpoint_allowed(endpoint) is allowed


def test_unset_allows_everything(no_allowlist):
    assert llm_settings.endpoint_allowlist() is None
    assert llm_settings.is_endpoint_allowed("https://anything.example/v1")


@pytest.mark.parametrize("value", [" , ", "https://", "*."])
def test_set_without_a_usable_entry_allows_nothing(monkeypatch, value):
    monkeypatch.setenv("LLM_ENDPOINT_ALLOWLIST", value)
    assert not llm_settings.is_endpoint_allowed("https://anything.example/v1")


def test_whitespace_only_means_unset(monkeypatch):
    monkeypatch.setenv("LLM_ENDPOINT_ALLOWLIST", "   ")
    assert llm_settings.endpoint_allowlist() is None


# ------------------------------------------------------------- chat time
def _bot(llm):
    from models.bots import Bots
    from services.bot_config import set_api_key

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"a-{uuid.uuid4().hex[:6]}", name="A",
                      config_json={"llm": {"model": "m", **llm}})
    set_api_key(bot.id, "k")
    return bot


def test_off_list_bot_endpoint_refuses_without_falling_back(isolated_db, allowlist, monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.internal/v1")  # allowed default
    bot = _bot({"api_endpoint": "https://collector.evil.io/v1/secret-path"})

    assert llm_settings.resolve(bot.id).missing == ["endpoint"]
    with pytest.raises(llm_settings.LLMNotConfigured):
        llm_settings.require(bot.id)
    assert "collector.evil.io" in caplog.text
    assert "secret-path" not in caplog.text


def test_off_list_deployment_default_refuses(isolated_db, allowlist, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    assert llm_settings.resolve(_bot({}).id).missing == ["endpoint"]


def test_on_list_endpoint_is_used(isolated_db, allowlist):
    bot = _bot({"api_endpoint": "https://my-res.openai.azure.com/openai"})
    assert llm_settings.require(bot.id).endpoint == "https://my-res.openai.azure.com/openai"


def test_chat_refuses_with_the_not_configured_reply(isolated_db, allowlist, monkeypatch):
    from agents.conversation_agent import ConversationSessionManager
    from services.agent_service import AgentService

    built = []
    monkeypatch.setattr("agents.conversation_agent.ChatOpenAI", lambda **kw: built.append(kw) or object())
    svc = AgentService()
    svc.session_manager = ConversationSessionManager()
    bot = _bot({"api_endpoint": "https://collector.evil.io/v1"})

    out = svc.process_message(bot_id=bot.id, user_id="u1", session_id="s1", page_id="p",
                              message="hello", stream=False)

    assert out == (llm_settings.NOT_CONFIGURED_REPLY, [])
    assert built == []


# ------------------------------------------------------------- save time
def _issues(endpoint):
    cfg = {"llm": {"api_endpoint": endpoint}}
    return [i for i in validate_config(cfg, ValidationContext()) if i.key.startswith("R24_")], cfg


def test_off_list_endpoint_blocks_save_and_cannot_be_forced(allowlist):
    issues, cfg = _issues("https://collector.evil.io/v1")
    assert [i.field for i in issues] == ["llm.api_endpoint"]
    assert "collector.evil.io" in issues[0].message
    assert blocking_format_issues(issues, cfg, None) == issues


def test_on_list_endpoint_saves(allowlist):
    assert _issues("https://llm.internal/v1/")[0] == []


def test_no_rule_when_unset(no_allowlist):
    assert _issues("https://collector.evil.io/v1")[0] == []


def test_templates_and_new_bots_are_checked_too(allowlist):
    from services.config_gate import check_new_config

    with pytest.raises(HTTPException) as exc:
        check_new_config({"llm": {"api_endpoint": "https://collector.evil.io/v1"}})
    assert exc.value.status_code == 422
    assert exc.value.detail["can_force"] is False


# ------------------------------------------------------------- operator view
def test_prod_without_allowlist_warns(no_allowlist, monkeypatch):
    monkeypatch.setenv("MODE", "prod")
    assert [w["code"] for w in llm_settings.endpoint_policy_warnings()] == ["llm_endpoint_unrestricted"]
    monkeypatch.setenv("MODE", "dev")
    assert llm_settings.endpoint_policy_warnings() == []


def test_blocked_deployment_default_warns(allowlist, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    (w,) = llm_settings.endpoint_policy_warnings()
    assert w["code"] == "llm_default_endpoint_not_allowed"
    assert "api.openai.com" in w["message"] and "/v1" not in w["message"]
