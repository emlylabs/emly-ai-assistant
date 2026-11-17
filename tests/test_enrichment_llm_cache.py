"""N9: enrichment follows the bot's current model, endpoint and key.

The enrichment worker cached one ChatOpenAI client per bot for the life
of the process, so after an owner changed the bot's model, endpoint or
API key, classification kept going to the old ones until a restart.
"""
from __future__ import annotations

import uuid

import pytest

from services import llm_settings


@pytest.fixture
def built(isolated_db, monkeypatch):
    import langchain_openai

    from services import enrichment

    for name in (llm_settings.MODEL_ENV, llm_settings.ENDPOINT_ENV, llm_settings.API_KEY_ENV,
                 llm_settings.ALLOWLIST_ENV):
        monkeypatch.delenv(name, raising=False)
    calls = []

    class _Fake:
        def __init__(self, **kw):
            calls.append(kw)
            self.kw = kw

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _Fake)
    enrichment._LLM_CACHE.clear()
    yield enrichment, calls
    enrichment._LLM_CACHE.clear()


def _bot(model="m1", endpoint="https://one.example/v1", key="k1"):
    from models.bots import Bots
    from services.bot_config import set_api_key

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"e-{uuid.uuid4().hex[:6]}", name="E",
                      config_json={"llm": {"model": model, "api_endpoint": endpoint}})
    set_api_key(bot.id, key)
    return bot


def _set_llm(bot, **llm):
    from models.bots import Bots

    Bots.update_config(bot.id, {"llm": llm})


def test_client_is_reused_while_settings_are_unchanged(built):
    enrichment, calls = built
    bot = _bot()
    first = enrichment._build_llm_for_bot(bot.id)
    assert enrichment._build_llm_for_bot(bot.id) is first
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["model", "endpoint"])
def test_config_change_builds_a_new_client(built, change):
    enrichment, calls = built
    bot = _bot()
    enrichment._build_llm_for_bot(bot.id)
    llm = {"model": "m1", "api_endpoint": "https://one.example/v1"}
    llm["model" if change == "model" else "api_endpoint"] = "m2" if change == "model" else "https://two.example/v1"
    _set_llm(bot, **llm)

    client = enrichment._build_llm_for_bot(bot.id)
    assert (client.kw["model"], client.kw["base_url"]) == (llm["model"], llm["api_endpoint"])
    assert len(calls) == 2


def test_key_rotation_builds_a_new_client(built):
    from services.bot_config import set_api_key

    enrichment, calls = built
    bot = _bot()
    enrichment._build_llm_for_bot(bot.id)
    set_api_key(bot.id, "k2")
    assert enrichment._build_llm_for_bot(bot.id).kw["api_key"] == "k2"


def test_unconfigured_bot_drops_the_cached_client(built):
    enrichment, _ = built
    bot = _bot()
    enrichment._build_llm_for_bot(bot.id)
    _set_llm(bot, model="m1")  # endpoint removed
    assert enrichment._build_llm_for_bot(bot.id) is None
    assert bot.id not in enrichment._LLM_CACHE


def test_cache_does_not_hold_the_plain_key(built):
    enrichment, _ = built
    bot = _bot(key="sk-very-secret")
    enrichment._build_llm_for_bot(bot.id)
    fingerprint, _client = enrichment._LLM_CACHE[bot.id]
    assert "sk-very-secret" not in fingerprint
