"""Authorize URL, code exchange, and refresh for Zoho CRM and Salesforce.

The client id and secret come from the bot's connection row, not from
deployment environment variables. The redirect URI is always the one this
app computes; callers cannot pass a different one through to the provider
without going through ``oauth_redirect_uri``.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlencode

from crm.contracts import CrmAuthError
from crm.http import error_code, make_client, read_json, send
from crm.providers import (
    SALESFORCE_SCOPES,
    SALESFORCE_TOKEN_SECONDS,
    ZOHO_SCOPES,
    allow_salesforce_instance,
    allow_salesforce_login,
    allow_zoho_accounts,
    allow_zoho_api_domain,
)


@dataclass(frozen=True)
class TokenGrant:
    access_token: str
    refresh_token: Optional[str]
    expires_in: int
    api_domain: Optional[str] = None
    scopes: Optional[str] = None


def _scopes(payload: dict) -> Optional[str]:
    raw = payload.get("scopes", payload.get("scope"))
    if isinstance(raw, list):
        return " ".join(str(item) for item in raw if str(item).strip()) or None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _expires_in(payload: dict, default: Optional[int] = None) -> int:
    if default is not None and payload.get("expires_in") in (None, ""):
        return default
    try:
        value = int(payload.get("expires_in"))
    except (TypeError, ValueError):
        raise CrmAuthError("no expires_in") from None
    if value <= 0:
        raise CrmAuthError("no expires_in")
    return value


def _grant(payload: dict, *, require_refresh: bool, default_expires_in: Optional[int] = None) -> TokenGrant:
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise CrmAuthError(error_code(payload) or "no access token")
    refresh = payload.get("refresh_token")
    refresh_token = refresh if isinstance(refresh, str) and refresh else None
    if require_refresh and not refresh_token:
        raise CrmAuthError(error_code(payload) or "no refresh token")
    # Zoho names the API host ``api_domain``; Salesforce names it ``instance_url``.
    api_domain = payload.get("api_domain") or payload.get("instance_url")
    return TokenGrant(
        access_token=access,
        refresh_token=refresh_token,
        expires_in=_expires_in(payload, default_expires_in),
        api_domain=api_domain if isinstance(api_domain, str) and api_domain else None,
        scopes=_scopes(payload),
    )


def pkce_pair() -> tuple[str, str]:
    """A PKCE ``(code_verifier, S256 code_challenge)`` pair (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _FormOAuth:
    def __init__(self, client_factory=None):
        self._client_factory = client_factory or make_client

    def allow_api_domain(self, url: str) -> str:
        """Check an API host a token response named. Raises ValueError when it isn't one of ours."""
        raise ValueError("this CRM does not name an API host")

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
    def allow_api_domain(self, url: str) -> str:
        return allow_zoho_api_domain(url)

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


class SalesforceOAuth(_FormOAuth):
    """Web server flow with PKCE against login.salesforce.com or test.salesforce.com.

    The token response carries ``instance_url`` (the org's API host) and no
    ``expires_in``. A refresh returns a new refresh token only when the org
    turned on rotation; otherwise the caller keeps the one it has.
    """

    def allow_api_domain(self, url: str) -> str:
        return allow_salesforce_instance(url)

    def authorize_url(
        self, *, client_id: str, redirect_uri: str, state: str, accounts_server: str, code_challenge: str
    ) -> str:
        login = allow_salesforce_login(accounts_server)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(SALESFORCE_SCOPES),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            },
            quote_via=quote,
        )
        return f"{login}/services/oauth2/authorize?{query}"

    def _checked(self, grant: TokenGrant) -> TokenGrant:
        if not grant.api_domain:
            raise CrmAuthError("no instance_url")
        try:
            instance = allow_salesforce_instance(grant.api_domain)
        except ValueError:
            raise CrmAuthError("token response named an unexpected API host") from None
        return TokenGrant(
            access_token=grant.access_token,
            refresh_token=grant.refresh_token,
            expires_in=grant.expires_in,
            api_domain=instance,
            scopes=grant.scopes,
        )

    async def exchange(
        self,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        accounts_server: str,
        code_verifier: str,
    ) -> TokenGrant:
        login = allow_salesforce_login(accounts_server)
        payload = await self._post(
            f"{login}/services/oauth2/token",
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )
        return self._checked(
            _grant(payload, require_refresh=True, default_expires_in=SALESFORCE_TOKEN_SECONDS)
        )

    async def refresh(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        accounts_server: Optional[str] = None,
    ) -> TokenGrant:
        login = allow_salesforce_login(accounts_server or "")
        payload = await self._post(
            f"{login}/services/oauth2/token",
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )
        return self._checked(
            _grant(payload, require_refresh=False, default_expires_in=SALESFORCE_TOKEN_SECONDS)
        )
