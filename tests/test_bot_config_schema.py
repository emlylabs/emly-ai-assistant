"""Schema contract for ``services.bot_config.BotConfigV1``.

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

from services.bot_config import BotConfigV1


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
    cfg = BotConfigV1.model_validate(payload)
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
        BotConfigV1.model_validate(payload)


def test_camel_case_inside_c_forms_entry_rejected():
    payload = {
        "c_forms_selected": [
            {"x": {"formSchema": {}, "trigger": {}}},
        ]
    }
    with pytest.raises(ValidationError):
        BotConfigV1.model_validate(payload)
