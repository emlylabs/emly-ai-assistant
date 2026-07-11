"""Static-token strategy.

Admin pastes a long-lived token; we store it Fernet-encrypted and hand
it back unchanged on every outbound. Used by Telegram, the static path
for WhatsApp Cloud, and Google Chat (where the "token" is the entire
service account JSON).

There's no flow — `requires_oauth_callback = False`. The strategy only
needs to know which field on ``secrets`` holds the bearer token. Each
adapter passes that via ``token_field``; the default ``"access_token"``
covers the common case.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Type

from pydantic import BaseModel

from channels.auth.base import AuthStrategy

if TYPE_CHECKING:
    from models.bot_channels import BotChannelModel


class StaticToken(AuthStrategy):
    requires_oauth_callback = False

    def __init__(self, secrets_model: Type[BaseModel], token_field: str = "access_token"):
        self.secrets_model = secrets_model
        self._token_field = token_field

    async def get_access_token(self, channel: "BotChannelModel") -> str:
        from channels.dispatcher import decrypt_secrets

        secrets = decrypt_secrets(channel)
        token = getattr(secrets, self._token_field, None)
        if not token:
            raise RuntimeError(
                f"Static-token strategy: secrets has no field {self._token_field!r}"
            )
        return token
