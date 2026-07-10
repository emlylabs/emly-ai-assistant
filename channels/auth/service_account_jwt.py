"""Service-account JWT bearer (RFC 7523).

Google Chat authenticates an "app" by signing a JWT assertion with the
service account's private key, exchanging it at
``https://oauth2.googleapis.com/token`` for a bearer token. No end-user
flow; no client_credentials; just a signed assertion.

Subclass override decides audience and scopes.
"""
from __future__ import annotations

import logging
import time
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Dict, List, Tuple

import httpx
import jwt as pyjwt
from pydantic import BaseModel

from channels.auth._http import make_client
from channels.auth.base import AuthStrategy

if TYPE_CHECKING:
    from models.bot_channels import BotChannelModel

log = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass
class _CachedToken:
    access_token: str
    expires_at: datetime


class ServiceAccountJWT(AuthStrategy):
    requires_oauth_callback = False

    def __init__(self):
        self._cache: Dict[Tuple[str, str], _CachedToken] = {}

    @abstractmethod
    def scopes(self) -> List[str]:
        ...

    @abstractmethod
    def service_account_dict(self, secrets: BaseModel) -> dict:
        """Return the SA JSON dict from the secrets blob."""

    async def get_access_token(self, channel: "BotChannelModel") -> str:
        from channels.dispatcher import decrypt_secrets

        secrets = decrypt_secrets(channel)
        sa = self.service_account_dict(secrets)
        cache_key = (channel.id, ",".join(self.scopes()))
        cached = self._cache.get(cache_key)
        if cached and cached.expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
            return cached.access_token

        now = int(time.time())
        assertion = pyjwt.encode(
            {
                "iss": sa["client_email"],
                "scope": " ".join(self.scopes()),
                "aud": GOOGLE_TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            },
            sa["private_key"],
            algorithm="RS256",
        )
        async with make_client() as client:
            try:
                resp = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                )
            except httpx.TimeoutException as e:
                log.warning(
                    "Token mint timed out at %s for sa=%s (%s) — check container egress to oauth2.googleapis.com",
                    GOOGLE_TOKEN_URL, sa.get("client_email"), e.__class__.__name__,
                )
                raise
            except httpx.TransportError as e:
                log.warning(
                    "Token mint transport error at %s for sa=%s: %s",
                    GOOGLE_TOKEN_URL, sa.get("client_email"), e,
                )
                raise
            if resp.status_code >= 400:
                log.warning(
                    "Token mint rejected status=%s sa=%s body=%s",
                    resp.status_code, sa.get("client_email"), resp.text[:300],
                )
                resp.raise_for_status()
            body = resp.json()

        access_token = body["access_token"]
        expires_in = int(body.get("expires_in", 3600))
        self._cache[cache_key] = _CachedToken(
            access_token=access_token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )
        return access_token
