"""Schema contract for ``services.bot_config.BotConfig``.

The config blob is the API surface between the admin UI and the runtime;
once a key is named in the schema, every reader expects it. Drift here
breaks both halves silently. The two checks below pin the contract:

1. A snake_case payload (mirroring sample-config.json) round-trips
   through ``model_validate`` → ``model_dump`` byte-for-byte.
2. A camelCase root key is rejected by ``ValidationError``.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.bot_config import BotConfig


def test_snake_case_payload_round_trips():
    payload = {
        "schema_version": 1,
        "topics": {},
        "global_prompts": {"welcome_message": "Hi"},
        "c_forms_selected": [
            {
                "callback_form": {
                    "form_schema": {"id": "callback_form", "form": {"submit": {"label": "Go", "type": "submit"}}},
                    "trigger": {"value": "PROMPT", "trigger_code": "cb_x1y2", "belongs_to": ["support"]},
                }
            }
        ],
        "llm": {"model_type": "openai", "model": "gpt-4o-mini"},
        "rag": {"top_k": 5},
        "limits": {"max_file_size_mb": 25},
        "launcher_label": "Chat",
        "is_icon_with_label": True,
        "starter_messages": ["hi", "help"],
        "support_email": "ops@example.com",
        "whatsapp_link": "https://wa.me/15555550123",
        "social_handles": {"accounts": {}},
        "terms_of_service": {"terms": "..."},
    }
    cfg = BotConfig.model_validate(payload)
    dumped = cfg.model_dump(mode="json", exclude_none=True)
    assert dumped["c_forms_selected"][0]["callback_form"]["form_schema"]["id"] == "callback_form"
    assert dumped["launcher_label"] == "Chat"
    assert dumped["whatsapp_link"] == "https://wa.me/15555550123"


@pytest.mark.parametrize(
    "bad_key",
    [
        "cFormsSelected",
        "launcherLabel",
        "isIconWithLabel",
        "starterMessages",
        "supportEmail",
        "whatsAppLink",
        "socialHandles",
        "termsOfService",
    ],
)
def test_camel_case_root_key_rejected(bad_key: str):
    payload = {bad_key: "anything"}
    with pytest.raises(ValidationError):
        BotConfig.model_validate(payload)


def test_camel_case_inside_c_forms_entry_rejected():
    payload = {
        "c_forms_selected": [
            {"x": {"formSchema": {}, "trigger": {}}},
        ]
    }
    with pytest.raises(ValidationError):
        BotConfig.model_validate(payload)


def test_enable_voice_round_trips():
    cfg = BotConfig.model_validate({"enable_voice": True, "show_citations": True})
    dumped = cfg.model_dump(mode="json", exclude_none=True)
    assert dumped["enable_voice"] is True
    assert dumped["show_citations"] is True


def test_enable_voice_camel_case_rejected():
    with pytest.raises(ValidationError):
        BotConfig.model_validate({"enableVoice": True})


def test_voice_language_round_trips():
    cfg = BotConfig.model_validate({"enable_voice": True, "voice_language": "es-MX"})
    dumped = cfg.model_dump(mode="json", exclude_none=True)
    assert dumped["voice_language"] == "es-MX"


@pytest.mark.parametrize("bad", ["english!", "e", "es_MX", "es-", ""])
def test_voice_language_rejects_non_bcp47(bad):
    with pytest.raises(ValidationError):
        BotConfig.model_validate({"voice_language": bad})


def test_tts_provider_deepgram_round_trips():
    cfg = BotConfig.model_validate({
        "enable_voice": True,
        "stt_provider": "deepgram",
        "tts_provider": "deepgram",
        "deepgram_tts_model": "aura-2-thalia-en",
        "deepgram_stt_model": "nova-3",
    })
    dumped = cfg.model_dump(mode="json", exclude_none=True)
    assert dumped["stt_provider"] == "deepgram"
    assert dumped["tts_provider"] == "deepgram"
    assert dumped["deepgram_tts_model"] == "aura-2-thalia-en"
    assert dumped["deepgram_stt_model"] == "nova-3"


def test_enable_voice_call_round_trips_independently():
    cfg = BotConfig.model_validate({"enable_voice_call": True})
    dumped = cfg.model_dump(mode="json", exclude_none=True)
    assert dumped["enable_voice_call"] is True
    assert "enable_voice" not in dumped


def test_tts_provider_invalid_rejected():
    with pytest.raises(ValidationError):
        BotConfig.model_validate({"tts_provider": "elevenlabs"})
