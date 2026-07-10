"""OAuth 2.0 authorization-code base.

RFC 6749 auth_code grant — Slack, WhatsApp Cloud (Meta Embedded
Signup), and any future Discord/LinkedIn-style platforms.

Concrete reusable bits live here:
- ``build_authorize_url(state, redirect_uri, scopes)`` — assembles the
  redirect that starts the flow.
- ``exchange_code(code, redirect_uri)`` — POST to the token endpoint.
- ``refresh(refresh_token)`` — same shape, ``grant_type=refresh_token``.
- ``get_access_token(channel)`` — cache + refresh-on-demand + atomic
  persist of the rotated token.

Per-platform overrides:
- ``parse_token_response(body) -> TokenSet`` — Slack nests under
  ``team``, Meta is flat, etc.
- ``extract_install_identity(body) -> str`` — what to write into
  ``BotChannel.external_id``.
- ``post_install_hook(channel, token_set)`` — optional, e.g. Meta's
  webhook subscription POST.
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Dict, List, Optional
from urllib.parse import urlencode

from pydantic import BaseModel

from channels.auth._http import make_client
from channels.auth.base import AuthStrategy, InstallMetadata

if TYPE_CHECKING:
    from models.bot_channels import BotChannelModel

log = logging.getLogger(__name__)


@dataclass
class TokenSet:
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    extra: Dict = field(default_factory=dict)

    def is_expiring_soon(self, leeway_seconds: int = 60) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at - timedelta(seconds=leeway_seconds)


def _serialize(token_set: TokenSet) -> dict:
    return {
        "access_token": token_set.access_token,
        "refresh_token": token_set.refresh_token,
        "expires_at": token_set.expires_at.isoformat() if token_set.expires_at else None,
        "extra": token_set.extra,
    }


def _deserialize(blob: dict) -> TokenSet:
    return TokenSet(
        access_token=blob["access_token"],
        refresh_token=blob.get("refresh_token"),
        expires_at=datetime.fromisoformat(blob["expires_at"]) if blob.get("expires_at") else None,
        extra=blob.get("extra") or {},
    )


class OAuth2AuthCodeBase(AuthStrategy):
    requires_oauth_callback = True
    allows_direct_static_install = False

    authorize_url: str = ""
    token_url: str = ""
    use_pkce: bool = False

    def __init__(self, scopes: List[str], client_id_env: str, client_secret_env: str):
        self._scopes = scopes
        self._client_id_env = client_id_env
        self._client_secret_env = client_secret_env

    def _app_credentials(self) -> Dict[str, str]:
        import os

        client_id = os.environ.get(self._client_id_env)
        client_secret = os.environ.get(self._client_secret_env)
        if not client_id or not client_secret:
            raise RuntimeError(
                f"OAuth app credentials missing: set {self._client_id_env} and "
                f"{self._client_secret_env} on the deployment."
            )
        return {"client_id": client_id, "client_secret": client_secret}

    def build_authorize_url(self, state: str, redirect_uri: str, extra_params: Optional[Dict[str, str]] = None) -> str:
        params = {
            "client_id": self._app_credentials()["client_id"],
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._scopes),
            "state": state,
            "response_type": "code",
        }
        if extra_params:
            params.update(extra_params)
        return f"{self.authorize_url}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        creds = self._app_credentials()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        }
        async with make_client() as client:
            resp = await client.post(self.token_url, data=data)
            resp.raise_for_status()
            body = resp.json()
        token_set = self.parse_token_response(body)
        log.info("OAuth exchange_code succeeded for %s", self.__class__.__name__)
        return token_set

    async def refresh(self, refresh_token: str) -> TokenSet:
        creds = self._app_credentials()
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        }
        async with make_client() as client:
            resp = await client.post(self.token_url, data=data)
            resp.raise_for_status()
            body = resp.json()
        return self.parse_token_response(body)

    # Single-flight refresh per channel. Two concurrent inbound webhooks
    # for the same channel both decrypt → both check expiry → both call
    # `refresh()` against the IDP. For providers with rotating refresh
    # tokens (Slack future-OAuth, Reddit, Discord) the second refresh
    # invalidates the first, and our `_persist_rotated_token` race
    # determines who wins persistence — net effect: one of the two
    # refresh tokens is silently broken. The lock makes refresh
    # serial-per-channel within this process. Multi-replica deploys
    # need a Redis-backed lock; that's part of the future-items queue.
    _refresh_locks: "Dict[str, asyncio.Lock]" = {}

    @classmethod
    def _get_refresh_lock(cls, channel_id: str) -> "asyncio.Lock":
        lock = cls._refresh_locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._refresh_locks[channel_id] = lock
        return lock

    async def get_access_token(self, channel: "BotChannelModel") -> str:
        from channels.dispatcher import decrypt_secrets

        secrets = decrypt_secrets(channel)
        token_set = self._token_set_from_secrets(secrets)
        if not token_set.is_expiring_soon():
            return token_set.access_token

        if not token_set.refresh_token:
            log.warning("Token expired/expiring for channel %s and no refresh_token available", channel.id)
            return token_set.access_token

        lock = self._get_refresh_lock(channel.id)
        async with lock:
            # Re-read after acquiring the lock — the prior holder may
            # have just refreshed; pick up their new token instead of
            # spending another round-trip on the IDP.
            from models.bot_channels import BotChannels as _BotChannels

            fresh_channel = _BotChannels.get_by_id(channel.id) or channel
            secrets = decrypt_secrets(fresh_channel)
            token_set = self._token_set_from_secrets(secrets)
            if not token_set.is_expiring_soon():
                return token_set.access_token

            new_set = await self.refresh(token_set.refresh_token)
            await self._persist_rotated_token(fresh_channel, secrets, new_set)
            return new_set.access_token

    def _token_set_from_secrets(self, secrets: BaseModel) -> TokenSet:
        # Default: secrets carry `access_token`, `refresh_token`, `expires_at`
        # at top level. Subclasses with idiosyncratic shapes override.
        data = secrets.model_dump()
        return TokenSet(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token"),
            expires_at=(
                datetime.fromisoformat(data["expires_at"])
                if data.get("expires_at")
                else None
            ),
            extra=data.get("token_extra") or {},
        )

    async def _persist_rotated_token(
        self, channel: "BotChannelModel", old_secrets: BaseModel, new_set: TokenSet
    ) -> None:
        from models.bot_channels import BotChannels
        from services.secrets import encrypt

        merged = old_secrets.model_dump()
        merged["access_token"] = new_set.access_token
        if new_set.refresh_token:
            merged["refresh_token"] = new_set.refresh_token
        if new_set.expires_at:
            merged["expires_at"] = new_set.expires_at.isoformat()
        merged_secrets = self.secrets_model.model_validate(merged)
        ciphertext = encrypt(merged_secrets.model_dump_json())
        BotChannels.update_credentials(channel.id, ciphertext)

    @abstractmethod
    def parse_token_response(self, body: dict) -> TokenSet:
        ...

    @abstractmethod
    def extract_install_identity(self, body: dict) -> str:
        ...

    async def post_install_hook(self, channel: "BotChannelModel", token_set: TokenSet) -> None:
        return None
