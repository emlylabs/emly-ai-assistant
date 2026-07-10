"""Admin CRUD for ``BotChannel`` rows.

Provisioning surface for static-token installs (Telegram, Teams,
WhatsApp Cloud static, Google Chat) and management surface for
OAuth-installed channels (Slack, WhatsApp Cloud OAuth) — listing,
healthcheck, secret rotation, soft/hard delete, per-install config
patches.

OAuth *initiation* lives in ``routes.channels`` (the redirect dance
needs to be public-but-state-verified). Static-token *creation* lives
here because it carries credentials and must be admin-authed.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from channels.base import InstallError
from channels.registry import all_adapters, get as registry_get
from models.admin_bot_memberships import ROLES as ALL_ROLES, AdminBotMemberships
from models.admin_users import AdminUserModel
from models.bot_channels import BotChannelModel, BotChannels
from models.bots import BotModel, Bots
from services.auth.dependencies import get_admin
from services.secrets import encrypt as fernet_encrypt

# Role groupings used by every mutation route. Reads (list, types,
# health) accept any member; writes require admin or owner; hard-delete
# (which calls platform-side ``revoke``) is owner-only — same precedent
# as ``delete_bot`` in routes/admin_bots.py.
WRITE_ROLES = ("owner", "admin")
OWNER_ONLY = ("owner",)

log = logging.getLogger(__name__)
router = APIRouter()


def _public_base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")


def _resolve_bot_for_admin(
    slug: str,
    admin: AdminUserModel,
    allowed_roles: tuple = ALL_ROLES,
) -> BotModel:
    """Resolve the bot and enforce membership at the requested role tier.

    Default ``allowed_roles=ALL_ROLES`` is a read gate — any member,
    including viewers, can fetch the row. Mutations pass
    ``WRITE_ROLES`` and hard-delete passes ``OWNER_ONLY``.
    """
    bot = Bots.get_by_slug(slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(status_code=404, detail="bot not found")
    membership = AdminBotMemberships.get(admin.id, bot.id)
    if membership is None or membership.role not in allowed_roles:
        # Same opaque 403 for "not a member" and "wrong role" — neither
        # case justifies leaking which one to the caller.
        raise HTTPException(status_code=403, detail="not a member of this bot")
    return bot


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ChannelCreate(BaseModel):
    type: str
    secrets: Dict[str, Any]
    config: Optional[Dict[str, Any]] = None


class ChannelSecretsUpdate(BaseModel):
    secrets: Dict[str, Any]


class ChannelConfigUpdate(BaseModel):
    config: Dict[str, Any]


class ChannelActiveUpdate(BaseModel):
    is_active: bool


class ChannelResponse(BaseModel):
    id: str
    bot_id: str
    type: str
    external_id: Optional[str]
    display_name: Optional[str]
    is_active: bool
    created_at: datetime
    secrets_rotated_at: Optional[datetime]
    config: Optional[Dict[str, Any]]
    secrets_redacted: Dict[str, Any]
    webhook_url: str


class ChannelTypeInfo(BaseModel):
    type: str
    requires_oauth_callback: bool
    allows_direct_static_install: bool
    install_addressing: str
    default_reply_mode: str
    supported_reply_modes: List[str]
    secrets_schema: Dict[str, Any]
    install_url_template: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _redact_secrets(secrets: Dict[str, Any]) -> Dict[str, Any]:
    """Reveal nothing sensitive — just last-4 of token-shaped values."""
    out: Dict[str, Any] = {}
    for k, v in secrets.items():
        if isinstance(v, str) and any(s in k.lower() for s in ("token", "secret", "password", "key")):
            out[k] = f"…{v[-4:]}" if len(v) > 4 else "…"
        elif isinstance(v, dict):
            out[k] = "<object>"
        elif isinstance(v, list):
            out[k] = f"<list[{len(v)}]>"
        else:
            out[k] = v
    return out


def _webhook_url_for(channel: BotChannelModel) -> str:
    base = _public_base_url()
    adapter = registry_get(channel.type)
    if adapter is None:
        return ""
    if adapter.install_addressing == "by_payload":
        return f"{base}/channels/{channel.type}/events"
    return f"{base}/channels/{channel.type}/{channel.id}/events"


def _stamp_managed_secrets(
    channel: BotChannelModel, secrets_model
) -> BotChannelModel:
    """Server-fill any secret fields the platform expects to know up-front
    but that depend on the channel row's id (i.e. its webhook URL). Today
    only Google Chat needs this — its inbound JWT audience must equal the
    public webhook URL, which we can only compute after insert. Re-encrypts,
    persists, and returns the refreshed channel if anything changed.
    """
    if channel.type != "google_chat":
        return channel
    canonical = _webhook_url_for(channel)
    current = getattr(secrets_model, "webhook_url", None)
    if current == canonical:
        return channel
    secrets_model.webhook_url = canonical
    BotChannels.update_credentials(
        channel.id, fernet_encrypt(secrets_model.model_dump_json())
    )
    refreshed = BotChannels.get_by_id(channel.id)
    return refreshed or channel


async def _channel_to_response(channel: BotChannelModel) -> ChannelResponse:
    secrets_redacted: Dict[str, Any] = {}
    if channel.credentials_encrypted:
        try:
            from channels.dispatcher import decrypt_secrets

            sec = decrypt_secrets(channel)
            secrets_redacted = _redact_secrets(sec.model_dump())
        except Exception:
            secrets_redacted = {"_": "decrypt-failed"}
    return ChannelResponse(
        id=channel.id,
        bot_id=channel.bot_id,
        type=channel.type,
        external_id=channel.external_id,
        display_name=channel.display_name,
        is_active=channel.is_active,
        created_at=channel.created_at,
        secrets_rotated_at=channel.secrets_rotated_at,
        config=channel.config_json,
        secrets_redacted=secrets_redacted,
        webhook_url=_webhook_url_for(channel),
    )


# ---------------------------------------------------------------------------
# Type metadata
# ---------------------------------------------------------------------------
@router.get("/channels/types", response_model=List[ChannelTypeInfo])
def list_channel_types(_admin: AdminUserModel = Depends(get_admin)):
    out: List[ChannelTypeInfo] = []
    base = _public_base_url()
    for adapter in all_adapters():
        if adapter.type == "web_widget":
            continue
        try:
            schema = adapter.auth.secrets_model.model_json_schema()
        except Exception:
            schema = {}
        if adapter.auth.requires_oauth_callback:
            install_url_template = f"{base}/channels/{adapter.type}/install?bot_id={{bot_id}}"
        else:
            install_url_template = ""
        out.append(
            ChannelTypeInfo(
                type=adapter.type,
                requires_oauth_callback=adapter.auth.requires_oauth_callback,
                allows_direct_static_install=adapter.auth.allows_direct_static_install,
                install_addressing=adapter.install_addressing,
                default_reply_mode=adapter.default_reply_mode,
                supported_reply_modes=sorted(adapter.supported_reply_modes),
                secrets_schema=schema,
                install_url_template=install_url_template,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Per-bot CRUD
# ---------------------------------------------------------------------------
@router.get("/bots/{slug}/channels", response_model=List[ChannelResponse])
async def list_channels_for_bot(slug: str, admin: AdminUserModel = Depends(get_admin)):
    bot = _resolve_bot_for_admin(slug, admin)
    rows = BotChannels.list_for_bot(bot.id)
    return [await _channel_to_response(r) for r in rows]


@router.post("/bots/{slug}/channels", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED)
async def create_channel(
    slug: str,
    payload: ChannelCreate,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot_for_admin(slug, admin, allowed_roles=WRITE_ROLES)
    adapter = registry_get(payload.type)
    if adapter is None:
        raise HTTPException(status_code=400, detail=f"unknown channel type {payload.type}")
    if adapter.auth.requires_oauth_callback and not adapter.auth.allows_direct_static_install:
        raise HTTPException(
            status_code=400,
            detail=f"{payload.type} requires OAuth install — use /channels/{payload.type}/install",
        )
    try:
        secrets_model = adapter.auth.validate_secrets(payload.secrets)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid secrets: {e}")

    metadata = None
    try:
        metadata = await adapter.extract_install_metadata(secrets_model)
    except NotImplementedError:
        metadata = None
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"install metadata fetch failed: {e}")

    external_id = metadata.external_id if metadata else None
    display_name = metadata.display_name if metadata else None

    if external_id:
        existing = BotChannels.get_by_external(payload.type, external_id)
        if existing is not None:
            if existing.bot_id != bot.id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"This {payload.type} install (external_id={external_id}) is "
                        f"already connected to a different bot. Uninstall it there first."
                    ),
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This {payload.type} install is already connected to this bot "
                    f"(channel={existing.id}). Rotate credentials via PUT /secrets instead."
                ),
            )

    channel_id = f"chn-{uuid.uuid4()}"
    ciphertext = fernet_encrypt(secrets_model.model_dump_json())
    channel = BotChannels.insert(
        id=channel_id,
        bot_id=bot.id,
        type=payload.type,
        external_id=external_id,
        credentials_encrypted=ciphertext,
        config_json=payload.config or {},
        display_name=display_name,
    )

    # Stamp any server-managed secret fields (e.g. Google Chat's webhook URL,
    # which doubles as the JWT audience) now that we know the channel id.
    channel = _stamp_managed_secrets(channel, secrets_model)

    # Adapter post-create hook: e.g. Telegram registers the webhook
    # with setWebhook so the operator doesn't have to. If this fails,
    # the row is unusable — roll it back (and call the adapter's
    # platform-side rollback hook so we don't leave dangling webhook
    # registrations) so the admin can retry without tripping the
    # (type, external_id) uniqueness check.
    post_create = getattr(adapter, "post_create_install", None)
    if post_create is not None:
        try:
            await post_create(channel, secrets_model)
        except InstallError as e:
            await _rollback_partial_install(adapter, channel, secrets_model)
            log.warning("post_create_install rejected channel=%s: %s", channel.id, e)
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            await _rollback_partial_install(adapter, channel, secrets_model)
            log.exception("post_create_install failed channel=%s", channel.id)
            raise HTTPException(
                status_code=400,
                detail=f"install validation failed: {e}",
            )

    return await _channel_to_response(channel)


async def _rollback_partial_install(adapter, channel: BotChannelModel, secrets) -> None:
    """Best-effort: ask the adapter to clean up any platform-side state
    its ``post_create_install`` may have created before failing, then
    delete the row. Adapter rollback errors are swallowed — we still
    must remove the row so the admin can retry.
    """
    try:
        await adapter.rollback_install(channel, secrets)
    except Exception:
        log.exception("rollback_install hook failed channel=%s", channel.id)
    BotChannels.delete(channel.id)


@router.put("/bots/{slug}/channels/{channel_id}/secrets", response_model=ChannelResponse)
async def update_channel_secrets(
    slug: str,
    channel_id: str,
    payload: ChannelSecretsUpdate,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot_for_admin(slug, admin, allowed_roles=WRITE_ROLES)
    channel = BotChannels.get_by_id(channel_id)
    if channel is None or channel.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="channel not found")
    adapter = registry_get(channel.type)
    if adapter is None:
        raise HTTPException(status_code=400, detail="adapter missing")
    try:
        secrets_model = adapter.auth.validate_secrets(payload.secrets)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid secrets: {e}")
    BotChannels.update_credentials(channel.id, fernet_encrypt(secrets_model.model_dump_json()))
    channel = BotChannels.get_by_id(channel_id)
    # Re-stamp server-managed fields after rotate so admins never have to
    # hand-type the canonical webhook URL.
    channel = _stamp_managed_secrets(channel, secrets_model)
    return await _channel_to_response(channel)


@router.put("/bots/{slug}/channels/{channel_id}/config", response_model=ChannelResponse)
async def update_channel_config(
    slug: str,
    channel_id: str,
    payload: ChannelConfigUpdate,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot_for_admin(slug, admin, allowed_roles=WRITE_ROLES)
    channel = BotChannels.get_by_id(channel_id)
    if channel is None or channel.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="channel not found")
    BotChannels.update_config(channel.id, payload.config)
    return await _channel_to_response(BotChannels.get_by_id(channel_id))


@router.put("/bots/{slug}/channels/{channel_id}/active", response_model=ChannelResponse)
async def set_channel_active(
    slug: str,
    channel_id: str,
    payload: ChannelActiveUpdate,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot_for_admin(slug, admin, allowed_roles=WRITE_ROLES)
    channel = BotChannels.get_by_id(channel_id)
    if channel is None or channel.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="channel not found")
    BotChannels.set_active(channel.id, payload.is_active)
    return await _channel_to_response(BotChannels.get_by_id(channel_id))


@router.delete("/bots/{slug}/channels/{channel_id}")
async def delete_channel(
    slug: str,
    channel_id: str,
    hard: bool = False,
    admin: AdminUserModel = Depends(get_admin),
):
    # Soft-delete needs WRITE; hard-delete (revokes platform-side state)
    # is owner-only, mirroring the ``delete_bot`` precedent.
    required = OWNER_ONLY if hard else WRITE_ROLES
    bot = _resolve_bot_for_admin(slug, admin, allowed_roles=required)
    channel = BotChannels.get_by_id(channel_id)
    if channel is None or channel.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="channel not found")
    if hard:
        adapter = registry_get(channel.type)
        if adapter is not None:
            try:
                await adapter.auth.revoke(channel)
            except Exception:
                log.exception("auth.revoke failed channel=%s — continuing with hard-delete", channel.id)
        BotChannels.delete(channel.id)
        return {"status": "deleted"}
    BotChannels.set_active(channel.id, False)
    return {"status": "deactivated"}


class OAuthStartRequest(BaseModel):
    type: str
    redirect_to: Optional[str] = None


class OAuthStartResponse(BaseModel):
    authorize_url: str


@router.post("/bots/{slug}/channels/oauth-start", response_model=OAuthStartResponse)
def oauth_start(
    slug: str,
    payload: OAuthStartRequest,
    admin: AdminUserModel = Depends(get_admin),
):
    """Mint an authorize URL for an OAuth-capable adapter so the UI can
    redirect the browser without leaking the admin JWT into a top-level
    navigation."""
    from channels.auth.state import (
        InvalidRedirect,
        issue as state_issue,
        validate_redirect_to,
    )

    bot = _resolve_bot_for_admin(slug, admin, allowed_roles=("owner", "admin"))
    adapter = registry_get(payload.type)
    if adapter is None or not adapter.auth.requires_oauth_callback:
        raise HTTPException(status_code=400, detail=f"{payload.type} does not support OAuth install")
    try:
        safe_redirect = validate_redirect_to(payload.redirect_to or f"/bots/{slug}/channels")
    except InvalidRedirect as e:
        raise HTTPException(status_code=400, detail=str(e))
    state = state_issue({
        "bot_id": bot.id,
        "type": payload.type,
        "admin_user_id": admin.id,
        "redirect_to": safe_redirect,
    })
    redirect_uri = f"{_public_base_url()}/channels/{payload.type}/oauth/callback"
    url = adapter.auth.build_authorize_url(state=state, redirect_uri=redirect_uri)  # type: ignore[attr-defined]
    return OAuthStartResponse(authorize_url=url)


@router.get("/bots/{slug}/channels/{channel_id}/health")
async def channel_health(
    slug: str,
    channel_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot_for_admin(slug, admin)
    channel = BotChannels.get_by_id(channel_id)
    if channel is None or channel.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="channel not found")
    adapter = registry_get(channel.type)
    if adapter is None:
        raise HTTPException(status_code=400, detail="adapter missing")
    return await adapter.healthcheck(channel)
