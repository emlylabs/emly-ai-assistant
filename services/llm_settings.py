"""Which model, endpoint and API key a bot's LLM calls use (D1).

Each value comes from the bot first and the deployment second:

    model     bot ``llm.model``         then env ``MODEL``
    endpoint  bot ``llm.api_endpoint``  then env ``OPENAI_BASE_URL``
    api key   bot's encrypted key       then env ``OPENAI_API_KEY``

There is no built-in default for any of them. A bot missing one refuses
to answer (``LLMNotConfigured``) instead of silently sending visitor
messages to a provider nobody chose. Env values are read at call time.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

log = logging.getLogger(__name__)

MODEL_ENV = "MODEL"
ENDPOINT_ENV = "OPENAI_BASE_URL"
API_KEY_ENV = "OPENAI_API_KEY"

# What a visitor sees when the bot can't answer because it isn't set up.
# Deliberately says nothing about why: that is for the admin UI.
NOT_CONFIGURED_REPLY = "Sorry, this assistant isn't available right now. Please try again later."


def _clean(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def deployment_model() -> Optional[str]:
    return _clean(os.environ.get(MODEL_ENV))


def deployment_endpoint() -> Optional[str]:
    return _clean(os.environ.get(ENDPOINT_ENV))


def deployment_api_key() -> Optional[str]:
    return _clean(os.environ.get(API_KEY_ENV))


@dataclass(frozen=True)
class LLMSettings:
    model: Optional[str]
    endpoint: Optional[str]
    api_key: Optional[str] = field(default=None, repr=False)

    @property
    def missing(self) -> List[str]:
        return [name for name, value in (
            ("model", self.model), ("endpoint", self.endpoint), ("api_key", self.api_key),
        ) if not value]

    def chat_kwargs(self) -> Dict[str, Any]:
        """``ChatOpenAI`` keyword arguments for these settings."""
        return {"model": self.model, "base_url": self.endpoint, "api_key": self.api_key}


class LLMNotConfigured(RuntimeError):
    def __init__(self, bot_id: Optional[str], missing: List[str]):
        self.bot_id = bot_id
        self.missing = list(missing)
        super().__init__(f"LLM not configured for bot {bot_id}: missing {', '.join(missing)}")


def _bot_llm_config(bot_id: str) -> Mapping[str, Any]:
    from models.bots import Bots

    bot = Bots.get_by_id(bot_id)
    llm = (bot.config_json or {}).get("llm") if bot else None
    return llm if isinstance(llm, Mapping) else {}


def resolve(
    bot_id: Optional[str],
    llm_cfg: Optional[Mapping[str, Any]] = None,
    *,
    model_override: Optional[str] = None,
) -> LLMSettings:
    """Settings for ``bot_id``. ``llm_cfg`` is the bot's ``llm`` config
    block; it is loaded from the live config when not given."""
    if llm_cfg is None:
        llm_cfg = _bot_llm_config(bot_id) if bot_id else {}
    api_key = None
    if bot_id:
        from services.bot_config import get_decrypted_api_key

        api_key = _clean(get_decrypted_api_key(bot_id))
    return LLMSettings(
        model=_clean(model_override) or _clean(llm_cfg.get("model")) or deployment_model(),
        endpoint=_clean(llm_cfg.get("api_endpoint")) or deployment_endpoint(),
        api_key=api_key or deployment_api_key(),
    )


def require(
    bot_id: Optional[str],
    llm_cfg: Optional[Mapping[str, Any]] = None,
    *,
    model_override: Optional[str] = None,
) -> LLMSettings:
    """Like ``resolve`` but raises ``LLMNotConfigured`` if anything is missing."""
    settings = resolve(bot_id, llm_cfg, model_override=model_override)
    if settings.missing:
        raise LLMNotConfigured(bot_id, settings.missing)
    return settings
