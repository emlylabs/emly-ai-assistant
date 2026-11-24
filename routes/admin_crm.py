"""Admin setup for a bot's Zoho CRM, HubSpot and Salesforce connections.

The admin pastes their own client id and secret. The redirect URL is
computed here and shown on the CRM page; it is not something they type,
and the callback sends that same URL back to the provider.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from channels.auth.state import issue, validate_redirect_to
from crm.oauth import HubSpotOAuth, SalesforceOAuth, ZohoOAuth, pkce_pair
from crm.providers import PROVIDERS, catalog, oauth_redirect_uri
from models.admin_users import AdminUserModel
from models.crm_connections import CrmConnections, to_public
from routes.admin_bot_channels import WRITE_ROLES, _resolve_bot_for_admin
from services.audit import audit
from services.auth.dependencies import get_admin
from services.secrets import encrypt

log = logging.getLogger(__name__)
router = APIRouter()


class SaveCrmCredentials(BaseModel):
    client_id: str = Field(min_length=1, max_length=512)
    client_secret: str | None = Field(default=None, max_length=2048)
    data_center: str | None = Field(default=None, max_length=16)


def _provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="unknown CRM")
    return provider


@router.get("/crm/providers")
def crm_providers(_admin: AdminUserModel = Depends(get_admin)):
    """Redirect URL, scopes, data centers, and the setup steps for the in-app guide."""
    return catalog()


@router.get("/bots/{slug}/crm")
def list_crm(slug: str, admin: AdminUserModel = Depends(get_admin)):
    bot = _resolve_bot_for_admin(slug, admin)
    return [to_public(row) for row in CrmConnections.list_for_bot(bot.id)]


@router.put("/bots/{slug}/crm/{provider}")
def save_crm(
    slug: str,
    provider: str,
    body: SaveCrmCredentials,
    admin: AdminUserModel = Depends(get_admin),
):
    provider = _provider(provider)
    bot = _resolve_bot_for_admin(slug, admin, allowed_roles=WRITE_ROLES)
    try:
        CrmConnections.upsert_credentials(
            bot.id, provider, body.client_id, body.client_secret, body.data_center
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    audit(
        "crm.credentials_saved",
        admin_id=admin.id,
        bot_id=bot.id,
        payload={"provider": provider, "data_center": body.data_center},
    )
    row = CrmConnections.get(bot.id, provider)
    return to_public(row)


@router.post("/bots/{slug}/crm/{provider}/oauth/start")
def start_crm_oauth(slug: str, provider: str, admin: AdminUserModel = Depends(get_admin)):
    provider = _provider(provider)
    bot = _resolve_bot_for_admin(slug, admin, allowed_roles=WRITE_ROLES)
    row = CrmConnections.get(bot.id, provider)
    if row is None or not row.client_secret_encrypted:
        raise HTTPException(status_code=400, detail="Save a client id and client secret first")
    redirect_uri = oauth_redirect_uri(provider)
    state_body = {
        "purpose": "crm",
        "bot_id": bot.id,
        "provider": provider,
        "redirect_to": validate_redirect_to(f"/bots/{bot.slug}/crm"),
    }
    challenge = None
    if provider == "salesforce":
        # PKCE. The state is signed but readable in the URL, so the verifier
        # rides in it encrypted; only this app can recover it at the callback.
        verifier, challenge = pkce_pair()
        state_body["pkce"] = encrypt(verifier)
    try:
        state = issue(state_body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    try:
        if provider == "zoho":
            if not row.accounts_server:
                raise HTTPException(status_code=400, detail="Choose a Zoho data center and save it")
            url = ZohoOAuth().authorize_url(
                client_id=row.client_id,
                redirect_uri=redirect_uri,
                state=state,
                accounts_server=row.accounts_server,
            )
        elif provider == "salesforce":
            if not row.accounts_server:
                raise HTTPException(status_code=400, detail="Choose Production or Sandbox and save it")
            url = SalesforceOAuth().authorize_url(
                client_id=row.client_id,
                redirect_uri=redirect_uri,
                state=state,
                accounts_server=row.accounts_server,
                code_challenge=challenge,
            )
        else:
            url = HubSpotOAuth().authorize_url(
                client_id=row.client_id,
                redirect_uri=redirect_uri,
                state=state,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    log.info("crm oauth start provider=%s bot=%s", provider, bot.id)
    return {"authorize_url": url}


@router.delete("/bots/{slug}/crm/{provider}")
def disconnect_crm(slug: str, provider: str, admin: AdminUserModel = Depends(get_admin)):
    provider = _provider(provider)
    bot = _resolve_bot_for_admin(slug, admin, allowed_roles=WRITE_ROLES)
    CrmConnections.disconnect(bot.id, provider)
    audit("crm.disconnected", admin_id=admin.id, bot_id=bot.id, payload={"provider": provider})
    return {"status": "disconnected"}
