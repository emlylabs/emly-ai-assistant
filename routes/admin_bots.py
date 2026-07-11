"""Bot CRUD + membership routes (Phase 4).

Path-based, per-bot URLs: ``/api/admin/bots`` for the cross-bot list /
create, ``/api/admin/bots/{slug}`` for per-bot ops.

Authz model:
- Read routes (``GET``) trust the JWT's ``bot_ids`` claim (fast path,
  accepts up-to-12h staleness on revocation).
- Destructive routes re-check ``admin_bot_memberships`` in DB so a
  revoked admin can't keep mutating after their JWT was issued.

Last-owner protection: revoking or downgrading an owner is rejected
with HTTP 409 if it would leave the bot without an active owner. The
membership-write helpers in ``models.admin_bot_memberships`` expose
``count_owners`` for this check.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from models.admin_bot_memberships import AdminBotMemberships, ROLES
from models.admin_users import AdminUserModel, AdminUsers
from models.bots import BotModel, Bots
from services.auth.dependencies import get_admin
from services.authz import require_member, require_owner, require_writer
from services.bot_templates import get_template_config, list_templates

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")


class BotCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    template_id: Optional[str] = Field(default=None, description="Optional template id; defaults to 'blank'.")


class TemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    preview_topics: List[str]


class BotPatch(BaseModel):
    name: Optional[str] = None


class BotResponse(BaseModel):
    id: str
    slug: str
    name: str
    is_active: bool
    is_deleted: bool
    config_schema_version: int
    config_version: int
    created_at: datetime
    updated_at: datetime


class MembershipGrant(BaseModel):
    admin_id: str
    role: str = Field(default="admin")


class MembershipUpdate(BaseModel):
    role: str


class MembershipResponse(BaseModel):
    admin_id: str
    bot_id: str
    role: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bot_to_response(bot: BotModel) -> BotResponse:
    return BotResponse(
        id=bot.id,
        slug=bot.slug,
        name=bot.name,
        is_active=bot.is_active,
        is_deleted=bot.is_deleted,
        config_schema_version=bot.config_schema_version,
        config_version=bot.config_version,
        created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


def _resolve_bot(slug: str) -> BotModel:
    bot = Bots.get_by_slug(slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    return bot


def _require_membership(admin: AdminUserModel, bot_id: str, allowed_roles: tuple = ROLES) -> None:
    """DB-checked membership for destructive routes. Phase 4 contract:
    don't trust the JWT's denormalized ``bot_ids`` claim for writes.
    Honours ``is_superadmin`` via the canonical helpers in ``services.authz``."""
    if "owner" in allowed_roles and "admin" not in allowed_roles:
        require_owner(admin, bot_id)
    else:
        require_writer(admin, bot_id)


# ---------------------------------------------------------------------------
# Bot CRUD
# ---------------------------------------------------------------------------
@router.get("/bot-templates", response_model=List[TemplateResponse])
def get_bot_templates(_admin: AdminUserModel = Depends(get_admin)):
    """Return the catalog the create-bot wizard renders as cards."""
    return [
        TemplateResponse(
            id=t.id, name=t.name, description=t.description, preview_topics=t.preview_topics
        )
        for t in list_templates()
    ]


@router.post("/bots", response_model=BotResponse, status_code=status.HTTP_201_CREATED)
def create_bot(
    payload: BotCreate,
    admin: AdminUserModel = Depends(get_admin),
):
    if not _SLUG_RE.match(payload.slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slug must be lowercase alphanumeric with optional hyphens (2-64 chars)",
        )
    if Bots.get_by_slug(payload.slug):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already in use")

    template_id = payload.template_id or "blank"
    try:
        config_json = get_template_config(template_id, payload.slug, payload.name)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown template id: {template_id}",
        )

    bot_id = f"bot-{uuid.uuid4()}"
    bot = Bots.insert(
        id=bot_id,
        slug=payload.slug,
        name=payload.name,
        config_json=config_json,
    )
    # Auto-grant the creator owner membership.
    AdminBotMemberships.grant(
        id=f"mbr-{uuid.uuid4()}",
        admin_id=admin.id,
        bot_id=bot_id,
        role="owner",
    )
    log.info(
        "Admin %s created bot id=%s slug=%s from template=%s",
        admin.id,
        bot_id,
        payload.slug,
        template_id,
    )
    return _bot_to_response(bot)


class WorkspaceBotSummary(BaseModel):
    """One row of the workspace overview. Phase 5 backend-backfill: this is
    what the admin UI's `/bots` page reads instead of paralleling 24 calls
    to `/dashboard/stats`. All numeric fields are nullable so a brand-new
    bot or a bot in a degraded state still serializes cleanly."""

    slug: str
    name: str
    is_active: bool
    msgs_24h: int
    sessions_24h: Optional[int] = None
    csat_avg: Optional[float] = None
    csat_count: Optional[int] = None
    p95_latency_ms: Optional[int] = None
    deflection_rate: Optional[float] = None
    active_channels: List[str] = []
    owner_email: Optional[str] = None
    updated_at: datetime


# Cached compute window. The summary endpoint is hit on every page load
# of /bots; without a cache the per-bot aggregations dominate Postgres
# CPU on multi-bot workspaces. 60s is acceptable freshness for a dense
# overview table; users can pull-to-refresh in the UI to bypass.
_SUMMARY_CACHE: dict[str, tuple[float, list[WorkspaceBotSummary]]] = {}
_SUMMARY_TTL = 60.0


@router.get("/bots/summary", response_model=List[WorkspaceBotSummary])
def list_bots_summary(admin: AdminUserModel = Depends(get_admin)):
    """Per-bot aggregates for the workspace overview table.

    Phase 5 of the backend-backfill plan. Iterates the admin's bot
    memberships and computes a 24h aggregate from each bot's report.
    Replaces the UI's prior N+1 over `/dashboard/stats`.
    """
    import time as _time

    cache_key = admin.id
    cached = _SUMMARY_CACHE.get(cache_key)
    if cached and (_time.time() - cached[0]) < _SUMMARY_TTL:
        return cached[1]

    from datetime import timedelta

    from models.bot_channels import BotChannels
    from models.emly_messages import EMLYMessages
    from models.emly_sessions import EMLYSessions

    memberships = AdminBotMemberships.list_for_admin(admin.id)
    bot_ids = [m.bot_id for m in memberships]
    summaries: List[WorkspaceBotSummary] = []
    now = datetime.now()
    since = now - timedelta(hours=24)
    for bot_id in bot_ids:
        bot = Bots.get_by_id(bot_id)
        if bot is None or bot.is_deleted:
            continue
        # Aggregates. Each call short-circuits to 0 when the bot has no
        # messages, so a brand-new bot doesn't trip on a missing window.
        try:
            report = EMLYMessages.get_report(bot.id, since, now) or {}
            msgs_24h = int(report.get("messages") or 0)
        except Exception:
            log.exception("get_report failed for bot=%s", bot.id)
            msgs_24h = 0
        try:
            sessions_24h = EMLYSessions.count_for_bot(bot.id)
        except Exception:
            sessions_24h = None
        try:
            channels = BotChannels.list_for_bot(bot.id)
            active_channels = sorted({c.type for c in channels if c.is_active})
        except Exception:
            active_channels = []
        # Owner email — pick the first owner. Falls back to None if the
        # owner row was hard-deleted (last-owner protection should make
        # this impossible but we don't crash the summary either way).
        owner_email = None
        try:
            for mem in AdminBotMemberships.list_for_bot(bot.id):
                if mem.role == "owner":
                    a = AdminUsers.get_by_id(mem.admin_id)
                    if a is not None:
                        owner_email = a.email
                        break
        except Exception:
            log.exception("owner lookup failed for bot=%s", bot.id)
        summaries.append(
            WorkspaceBotSummary(
                slug=bot.slug,
                name=bot.name,
                is_active=bot.is_active,
                msgs_24h=msgs_24h,
                sessions_24h=sessions_24h,
                # csat_avg / p95_latency_ms / deflection_rate populate
                # in Phases 6 & 7 once `get_report` learns those keys.
                csat_avg=report.get("csat_avg") if isinstance(report, dict) else None,
                csat_count=report.get("csat_count") if isinstance(report, dict) else None,
                p95_latency_ms=report.get("p95_latency_ms") if isinstance(report, dict) else None,
                deflection_rate=report.get("deflection_rate") if isinstance(report, dict) else None,
                active_channels=active_channels,
                owner_email=owner_email,
                updated_at=bot.updated_at,
            )
        )
    _SUMMARY_CACHE[cache_key] = (_time.time(), summaries)
    return summaries


@router.get("/bots", response_model=List[BotResponse])
def list_bots(admin: AdminUserModel = Depends(get_admin)):
    if admin.is_superadmin:
        # Superadmins see every active bot regardless of explicit
        # membership rows — `services.authz` documents this bypass.
        bots = Bots.list_active()
    else:
        memberships = AdminBotMemberships.list_for_admin(admin.id)
        bot_ids = [m.bot_id for m in memberships]
        bots = [b for b in (Bots.get_by_id(bid) for bid in bot_ids) if b is not None]
    return [_bot_to_response(b) for b in bots]


@router.get("/bots/{slug}", response_model=BotResponse)
def get_bot(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    require_member(admin, bot.id)
    return _bot_to_response(bot)


@router.patch("/bots/{slug}", response_model=BotResponse)
def patch_bot(
    slug: str,
    payload: BotPatch,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_membership(admin, bot.id, allowed_roles=("owner", "admin"))
    if payload.name is not None:
        from models.bots import Bot
        Bot.update(name=payload.name, updated_at=datetime.utcnow()).where(Bot.id == bot.id).execute()
    return _bot_to_response(_resolve_bot(slug))


@router.delete("/bots/{slug}", status_code=status.HTTP_202_ACCEPTED)
def soft_delete_bot(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    """Soft-delete-then-purge.

    Sets ``is_active=false``, ``is_deleted=true``, ``deleted_at=now``
    and returns immediately. The actual cascade (Qdrant points, files
    on disk, child rows) runs as an idempotent background task — see
    ``services.bot_purge.purge_deleted_bots``.
    """
    bot = _resolve_bot(slug)
    _require_membership(admin, bot.id, allowed_roles=("owner",))
    Bots.soft_delete(bot.id)
    log.info("Admin %s soft-deleted bot=%s", admin.id, bot.id)

    # Best-effort runtime invalidation; the registry will stop serving
    # this bot on its next ``get_handler`` call regardless.
    try:
        from utils.dependencies import AGENT_SERVICE_INSTANCE
        AGENT_SERVICE_INSTANCE.invalidate_bot(bot.id)
    except Exception:
        log.exception("invalidate_bot after soft-delete failed for bot=%s", bot.id)

    return {"status": "scheduled_for_purge", "bot_id": bot.id}


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
@router.get("/bots/{slug}/admins", response_model=List[MembershipResponse])
def list_bot_admins(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    if AdminBotMemberships.get(admin.id, bot.id) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this bot")
    rows = AdminBotMemberships.list_for_bot(bot.id)
    return [
        MembershipResponse(
            admin_id=m.admin_id, bot_id=m.bot_id, role=m.role, created_at=m.created_at,
        )
        for m in rows
    ]


@router.post("/bots/{slug}/admins", response_model=MembershipResponse, status_code=status.HTTP_201_CREATED)
def grant_membership(
    slug: str,
    payload: MembershipGrant,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_membership(admin, bot.id, allowed_roles=("owner",))
    if payload.role not in ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown role: {payload.role}")
    if AdminUsers.get_by_id(payload.admin_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found")
    if AdminBotMemberships.get(payload.admin_id, bot.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Membership already exists")

    m = AdminBotMemberships.grant(
        id=f"mbr-{uuid.uuid4()}",
        admin_id=payload.admin_id,
        bot_id=bot.id,
        role=payload.role,
    )
    return MembershipResponse(admin_id=m.admin_id, bot_id=m.bot_id, role=m.role, created_at=m.created_at)


@router.patch("/bots/{slug}/admins/{admin_id}", response_model=MembershipResponse)
def update_membership_role(
    slug: str,
    admin_id: str,
    payload: MembershipUpdate,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_membership(admin, bot.id, allowed_roles=("owner",))
    if payload.role not in ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown role: {payload.role}")

    # Last-owner protection: count + update inside a single transaction so two
    # concurrent demote-owner calls can't both observe ``count == 2`` and both
    # demote, leaving the bot ownerless.
    from db.db import DB
    with DB.atomic():
        existing = AdminBotMemberships.get(admin_id, bot.id)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
        if existing.role == "owner" and payload.role != "owner":
            if AdminBotMemberships.count_owners(bot.id) <= 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot downgrade the last owner — promote another owner first",
                )
        AdminBotMemberships.update_role(admin_id, bot.id, payload.role)
    refreshed = AdminBotMemberships.get(admin_id, bot.id)
    return MembershipResponse(
        admin_id=refreshed.admin_id, bot_id=refreshed.bot_id, role=refreshed.role, created_at=refreshed.created_at,
    )


@router.delete("/bots/{slug}/admins/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_membership(
    slug: str,
    admin_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_membership(admin, bot.id, allowed_roles=("owner",))
    # Last-owner protection: see notes on ``update_role`` above.
    from db.db import DB
    with DB.atomic():
        existing = AdminBotMemberships.get(admin_id, bot.id)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
        if existing.role == "owner" and AdminBotMemberships.count_owners(bot.id) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot revoke the last owner — promote another owner first",
            )
        AdminBotMemberships.revoke(admin_id, bot.id)
