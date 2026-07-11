"""OAuth 2.0 client_credentials base.

Used where a service authenticates as itself (no end-user redirect):
Microsoft Teams' Bot Framework calls AAD's
``/{tenant}/oauth2/v2.0/token`` with ``grant_type=client_credentials``
to mint a bearer for posting replies.

Tokens are short-lived (~1h); cached in-process keyed by (channel.id,
scope) and refreshed on demand.
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Dict, Tuple

from pydantic import BaseModel

from channels.auth._http import make_client
from channels.auth.base import AuthStrategy

if TYPE_CHECKING:
    from models.bot_channels import BotChannelModel

log = logging.getLogger(__name__)


@dataclass
class _CachedToken:
    access_token: str
    expires_at: datetime


class OAuth2ClientCredentials(AuthStrategy):
    requires_oauth_callback = False

    def __init__(self):
        self._cache: Dict[Tuple[str, str], _CachedToken] = {}

    @abstractmethod
    def token_url_for(self, secrets: BaseModel) -> str:
        ...

    @abstractmethod
    def scope_for(self, secrets: BaseModel) -> str:
        ...

    @abstractmethod
    def client_credentials_for(self, secrets: BaseModel) -> Tuple[str, str]:
        """Return ``(client_id, client_secret)`` for the install."""

    async def get_access_token(self, channel: "BotChannelModel") -> str:
        from channels.dispatcher import decrypt_secrets

        secrets = decrypt_secrets(channel)
        scope = self.scope_for(secrets)
        cache_key = (channel.id, scope)
        cached = self._cache.get(cache_key)
        if cached and cached.expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
            return cached.access_token

        client_id, client_secret = self.client_credentials_for(secrets)
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        }
        async with make_client() as client:
            resp = await client.post(self.token_url_for(secrets), data=data)
            resp.raise_for_status()
            body = resp.json()

        access_token = body["access_token"]
        expires_in = int(body.get("expires_in", 3600))
        self._cache[cache_key] = _CachedToken(
            access_token=access_token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )
        return access_token
