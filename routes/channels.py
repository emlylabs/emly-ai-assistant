"""Public channel webhook + OAuth surface.

URL pattern:

- ``POST /channels/{type}/events``                — payload-routed inbound (Slack, Teams)
- ``POST /channels/{type}/{channel_id}/events``   — path-routed inbound (Telegram, WhatsApp, GChat)
- ``GET  /channels/{type}/{channel_id}/events``   — handshake (WhatsApp hub.challenge)
- ``GET  /channels/{type}/install``               — admin-authed OAuth start
- ``GET  /channels/{type}/oauth/callback``        — public OAuth completion (state-verified)

Path-routed adapters: ``channel_id`` is the ``BotChannel.id`` UUID.
Payload-routed adapters: dispatcher reads the install key (Slack ``team_id``,
Teams ``tenant_id``) from the body and looks up the row by ``(type, external_id)``.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from channels.auth.state import InvalidRedirect, issue as state_issue
from channels.auth.state import validate_redirect_to as _validate_redirect_to
from channels.auth.state import verify as state_verify
from channels.dispatcher import handle_handshake, handle_inbound
from channels.registry import get as registry_get
from models.admin_bot_memberships import AdminBotMemberships
from models.admin_users import AdminUserModel
from models.bot_channels import BotChannelModel, BotChannels
from services.auth.dependencies import get_admin
from services.secrets import encrypt as fernet_encrypt

log = logging.getLogger(__name__)
router = APIRouter()


def _public_base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")


# ---------------------------------------------------------------------------
# Inbound webhook routes
# ---------------------------------------------------------------------------
@router.post("/channels/{channel_type}/events")
async def channel_events_payload_routed(
    channel_type: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    return await handle_inbound(channel_type, None, request, background_tasks)


@router.post("/channels/{channel_type}/{channel_id}/events")
async def channel_events_path_routed(
    channel_type: str,
    channel_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    return await handle_inbound(channel_type, channel_id, request, background_tasks)


@router.get("/channels/{channel_type}/{channel_id}/events")
async def channel_handshake(channel_type: str, channel_id: str, request: Request):
    return await handle_handshake(channel_type, channel_id, request)


# ---------------------------------------------------------------------------
# OAuth install / callback
# ---------------------------------------------------------------------------
@router.get("/channels/{channel_type}/install")
async def channel_oauth_install(
    channel_type: str,
    bot_id: str,
    redirect_to: Optional[str] = None,
    admin: AdminUserModel = Depends(get_admin),
):
    adapter = registry_get(channel_type)
    if adapter is None or not adapter.auth.requires_oauth_callback:
        raise HTTPException(status_code=404, detail=f"{channel_type} does not support OAuth install")
    if AdminBotMemberships.get(admin.id, bot_id) is None:
        raise HTTPException(status_code=403, detail="not a member of this bot")
    try:
        safe_redirect = _validate_redirect_to(redirect_to)
    except InvalidRedirect as e:
        raise HTTPException(status_code=400, detail=str(e))
    state = state_issue({
        "bot_id": bot_id,
        "type": channel_type,
        "admin_user_id": admin.id,
        "redirect_to": safe_redirect,
    })
    redirect_uri = f"{_public_base_url()}/channels/{channel_type}/oauth/callback"
    url = adapter.auth.build_authorize_url(state=state, redirect_uri=redirect_uri)  # type: ignore[attr-defined]
    return RedirectResponse(url=url, status_code=302)


@router.get("/channels/{channel_type}/oauth/callback")
async def channel_oauth_callback(
    channel_type: str,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        log.warning("OAuth callback returned error: %s", error)
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="code and state required")
    payload = state_verify(state)
    if payload is None or payload.get("type") != channel_type:
        raise HTTPException(status_code=400, detail="invalid or expired state")

    adapter = registry_get(channel_type)
    if adapter is None or not adapter.auth.requires_oauth_callback:
        raise HTTPException(status_code=404, detail="adapter not OAuth-capable")

    bot_id = payload["bot_id"]
    redirect_uri = f"{_public_base_url()}/channels/{channel_type}/oauth/callback"
    try:
        token_set = await adapter.auth.exchange_code(code, redirect_uri)  # type: ignore[attr-defined]
    except Exception:
        log.exception("OAuth code exchange failed type=%s", channel_type)
        raise HTTPException(status_code=502, detail="OAuth code exchange failed")

    external_id = adapter.auth.extract_install_identity(token_set.extra.get("raw_body") or {})  # type: ignore[attr-defined]
    if not external_id:
        external_id = token_set.extra.get("external_id") or ""

    secrets_dict = _build_secrets_from_token(adapter, token_set)
    secrets_model = adapter.auth.secrets_model.model_validate(secrets_dict)
    ciphertext = fernet_encrypt(secrets_model.model_dump_json())

    existing = BotChannels.get_by_external(channel_type, external_id) if external_id else None
    if existing and existing.bot_id != bot_id:
        # Same workspace / phone number / tenant is already connected to
        # a different bot. Don't let an OAuth flow silently steal it —
        # the operator has to uninstall there first.
        raise HTTPException(
            status_code=409,
            detail=(
                f"This {channel_type} install (external_id={external_id}) is already "
                f"connected to a different bot. Uninstall it from the other bot first."
            ),
        )
    if existing and existing.bot_id == bot_id:
        BotChannels.update_credentials(existing.id, ciphertext)
        channel = BotChannels.get_by_id(existing.id)
    else:
        channel_id = f"chn-{uuid.uuid4()}"
        channel = BotChannels.insert(
            id=channel_id,
            bot_id=bot_id,
            type=channel_type,
            external_id=external_id,
            credentials_encrypted=ciphertext,
            display_name=token_set.extra.get("display_name"),
        )

    try:
        await adapter.auth.post_install_hook(channel, token_set)  # type: ignore[attr-defined]
    except Exception:
        log.exception("post_install_hook failed channel=%s", channel.id)

    # `redirect_to` was validated at issue time and HMAC-sealed; trust the
    # signed payload here. Fall back to ``/`` if it wasn't supplied.
    redirect_to = payload.get("redirect_to") or "/"
    sep = "&" if "?" in redirect_to else "?"
    return RedirectResponse(url=f"{redirect_to}{sep}installed=1&channel_id={channel.id}", status_code=302)


def _build_secrets_from_token(adapter, token_set) -> dict:
    """Default OAuth-callback persistence: dump the token set fields into
    the secrets model. Adapters with extra required fields (Meta needs
    ``verify_token``, ``app_secret``) prompt the admin for those in a
    follow-up screen — we still create the row first with what we have.
    """
    base = {
        "access_token": token_set.access_token,
        "refresh_token": token_set.refresh_token,
        "expires_at": token_set.expires_at.isoformat() if token_set.expires_at else None,
    }
    base.update(token_set.extra or {})
    return base
