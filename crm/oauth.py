"""Authorize URL, code exchange, and refresh for Zoho and HubSpot.

The client id and secret come from the bot's connection row, not from
deployment environment variables. The redirect URI is always the one this
app computes; callers cannot pass a different one through to the provider
without going through ``oauth_redirect_uri``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlencode

from crm.contracts import CrmAuthError
from crm.http import error_code, make_client, read_json, send
from crm.providers import (
    HUBSPOT_AUTHORIZE,
    HUBSPOT_SCOPES,
    HUBSPOT_TOKEN,
    ZOHO_SCOPES,
    allow_zoho_accounts,
)


@dataclass(frozen=True)
class TokenGrant:
    access_token: str
    refresh_token: Optional[str]
    expires_in: int
    api_domain: Optional[str] = None
    hub_id: Optional[str] = None
    scopes: Optional[str] = None


def _scopes(payload: dict) -> Optional[str]:
    raw = payload.get("scopes", payload.get("scope"))
    if isinstance(raw, list):
        return " ".join(str(item) for item in raw if str(item).strip()) or None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _expires_in(payload: dict) -> int:
    try:
        value = int(payload.get("expires_in"))
    except (TypeError, ValueError):
        raise CrmAuthError("no expires_in") from None
    if value <= 0:
        raise CrmAuthError("no expires_in")
    return value


def _grant(payload: dict, *, require_refresh: bool) -> TokenGrant:
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise CrmAuthError(error_code(payload) or "no access token")
    refresh = payload.get("refresh_token")
    refresh_token = refresh if isinstance(refresh, str) and refresh else None
    if require_refresh and not refresh_token:
        raise CrmAuthError(error_code(payload) or "no refresh token")
    hub = payload.get("hub_id", payload.get("hubId"))
    api_domain = payload.get("api_domain")
    return TokenGrant(
        access_token=access,
        refresh_token=refresh_token,
        expires_in=_expires_in(payload),
        api_domain=api_domain if isinstance(api_domain, str) and api_domain else None,
        hub_id=str(hub) if hub not in (None, "") else None,
        scopes=_scopes(payload),
    )


class _FormOAuth:
    def __init__(self, client_factory=None):
        self._client_factory = client_factory or make_client

    async def _post(self, url: str, form: dict) -> dict:
        # Network and HTTP errors become CrmAuthError with no request body,
        # so a log of this exception cannot contain the client secret.
        try:
            response = await send(self._client_factory, "POST", url, form=form)
        except CrmAuthError:
            raise
        except Exception:
            raise CrmAuthError("token request failed") from None
        payload = read_json(response)
        if response.status_code != 200:
            raise CrmAuthError(error_code(payload) or f"token endpoint {response.status_code}")
        return payload


class ZohoOAuth(_FormOAuth):
    def authorize_url(self, *, client_id: str, redirect_uri: str, state: str, accounts_server: str) -> str:
        accounts = allow_zoho_accounts(accounts_server)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "scope": ",".join(ZOHO_SCOPES),
                "redirect_uri": redirect_uri,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            },
            quote_via=quote,
        )
        return f"{accounts}/oauth/v2/auth?{query}"

    async def exchange(
        self,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        accounts_server: str,
    ) -> TokenGrant:
        accounts = allow_zoho_accounts(accounts_server)
        payload = await self._post(
            f"{accounts}/oauth/v2/token",
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        return _grant(payload, require_refresh=True)

    async def refresh(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        accounts_server: Optional[str] = None,
    ) -> TokenGrant:
        # Zoho often omits refresh_token on refresh. The caller keeps the one it sent.
        accounts = allow_zoho_accounts(accounts_server or "")
        payload = await self._post(
            f"{accounts}/oauth/v2/token",
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )
        return _grant(payload, require_refresh=False)


class HubSpotOAuth(_FormOAuth):
    def authorize_url(self, *, client_id: str, redirect_uri: str, state: str) -> str:
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(HUBSPOT_SCOPES),
                "state": state,
            },
            quote_via=quote,
        )
        return f"{HUBSPOT_AUTHORIZE}?{query}"

    async def exchange(
        self,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        accounts_server: Optional[str] = None,
    ) -> TokenGrant:
        del accounts_server
        payload = await self._post(
            HUBSPOT_TOKEN,
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        return _grant(payload, require_refresh=True)

    async def refresh(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        accounts_server: Optional[str] = None,
    ) -> TokenGrant:
        # HubSpot rotates the refresh token. require_refresh so we never keep
        # going with a token the provider has already replaced and not shown us.
        del accounts_server
        payload = await self._post(
            HUBSPOT_TOKEN,
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )
        return _grant(payload, require_refresh=True)
