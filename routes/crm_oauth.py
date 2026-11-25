"""Browser redirect target for Zoho CRM and Salesforce.

Unauthenticated on purpose: the provider's browser redirect does not carry
the admin session. The signed ``state`` is the credential, same as the
chat-channel OAuth callback. The code is exchanged immediately, and only
for the redirect URI this app shows the admin.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from channels.auth.state import verify
from crm.contracts import CrmAuthError
from crm.oauth import SalesforceOAuth, ZohoOAuth
from crm.providers import (
    PROVIDERS,
    allow_salesforce_login,
    allow_zoho_accounts,
    allow_zoho_api_domain,
    oauth_redirect_uri,
    scope_string,
    zoho_api_domain_for_location,
)
from models.bots import Bots
from models.crm_connections import CrmConnections
from services.audit import audit
from services.secrets import decrypt

log = logging.getLogger(__name__)
router = APIRouter()

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")


def _go(slug: str | None, **query: str) -> RedirectResponse:
    if not slug or not _SLUG_RE.match(slug):
        location = "/"
    else:
        location = f"/bots/{slug}/crm"
    clean = {key: value for key, value in query.items() if value}
    if clean:
        location = f"{location}?{urlencode(clean)}"
    return RedirectResponse(location, status_code=302)


@router.get("/crm/{provider}/oauth/callback")
async def crm_oauth_callback(
    provider: str,
    code: str = "",
    state: str = "",
    error: str = "",
    location: str = "",
    accounts_server: str = Query(default="", alias="accounts-server"),
):
    if provider not in PROVIDERS:
        return _go(None, crm_error="state")
    payload = verify(state) if state else None
    if not payload or payload.get("purpose") != "crm" or payload.get("provider") != provider:
        return _go(None, crm_error="state")
    bot = Bots.get_by_id(str(payload.get("bot_id") or ""))
    if bot is None or bot.is_deleted:
        return _go(None, crm_error="state")
    slug = bot.slug
    row = CrmConnections.get(bot.id, provider)
    if row is None:
        return _go(slug, crm_error="missing")
    if error or not code or len(code) > 2048 or any(ch.isspace() for ch in code):
        CrmConnections.record_error(row.id, (error or "missing_code")[:200])
        return _go(slug, crm_error="denied")
    try:
        secret = decrypt(row.client_secret_encrypted)
    except InvalidToken:
        secret = None
    if not secret:
        CrmConnections.record_error(row.id, "stored client secret cannot be read")
        return _go(slug, crm_error="exchange")
    # The redirect URI is the one we publish. A query parameter cannot replace it.
    redirect_uri = oauth_redirect_uri(provider)
    try:
        if provider == "zoho":
            accounts = _zoho_accounts(accounts_server, row.accounts_server)
            grant = await ZohoOAuth().exchange(
                client_id=row.client_id,
                client_secret=secret,
                code=code,
                redirect_uri=redirect_uri,
                accounts_server=accounts,
            )
            api_domain = _zoho_domain(grant.api_domain, location)
            data_center = location.strip().lower() if location.strip().lower() in {
                "us", "eu", "in", "au", "jp", "ca", "sa", "cn"
            } else None
        elif provider == "salesforce":
            try:
                verifier = decrypt(str(payload.get("pkce") or ""))
            except InvalidToken:
                verifier = None
            if not verifier:
                raise CrmAuthError("PKCE verifier is missing")
            accounts = allow_salesforce_login(row.accounts_server or "")
            grant = await SalesforceOAuth().exchange(
                client_id=row.client_id,
                client_secret=secret,
                code=code,
                redirect_uri=redirect_uri,
                accounts_server=accounts,
                code_verifier=verifier,
            )
            # SalesforceOAuth already allowlisted instance_url.
            api_domain = grant.api_domain
            data_center = None
        else:
            raise CrmAuthError("unknown CRM")
        if not grant.refresh_token:
            raise CrmAuthError("no refresh token")
        now = datetime.utcnow()
        CrmConnections.save_tokens(
            row.id,
            access_token=grant.access_token,
            refresh_token=grant.refresh_token,
            issued_at=now,
            expires_in=grant.expires_in,
            expires_at=now + timedelta(seconds=grant.expires_in),
            api_domain=api_domain,
            scopes=grant.scopes or scope_string(provider),
            accounts_server=accounts,
            data_center=data_center,
        )
    except (CrmAuthError, ValueError) as exc:
        CrmConnections.record_error(row.id, str(exc))
        log.warning("crm oauth exchange failed provider=%s bot=%s error=%s", provider, bot.id, exc)
        return _go(slug, crm_error="exchange")
    except Exception:
        CrmConnections.record_error(row.id, "token exchange failed")
        log.exception("crm oauth exchange failed provider=%s bot=%s", provider, bot.id)
        return _go(slug, crm_error="exchange")
    audit("crm.connected", bot_id=bot.id, payload={"provider": provider})
    log.info("crm connected provider=%s bot=%s", provider, bot.id)
    return _go(slug, crm="connected")


def _zoho_accounts(from_callback: str, stored: str | None) -> str:
    """Prefer the accounts host Zoho sent back. A host outside the allowlist is ignored."""
    if from_callback:
        try:
            return allow_zoho_accounts(from_callback)
        except ValueError:
            log.warning("crm oauth ignored an unexpected Zoho accounts host")
    if stored:
        return allow_zoho_accounts(stored)
    raise CrmAuthError("Zoho accounts host is missing")


def _zoho_domain(api_domain: str | None, location: str) -> str:
    if api_domain:
        return allow_zoho_api_domain(api_domain)
    if location:
        return zoho_api_domain_for_location(location)
    raise CrmAuthError("no api_domain")
