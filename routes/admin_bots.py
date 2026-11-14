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
from services.audit import audit
from services.auth.dependencies import get_admin
from services.authz import require_bot_creator, require_member, require_owner, require_writer
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
    # When set, copies the source bot's ``config_json`` into the new bot
    # instead of starting from the template. The caller must have member
    # access to ``source_bot_id``; files/vectors are not copied (re-embed
    # is expensive and the v1 contract is "config only — re-upload files").
    source_bot_id: Optional[str] = Field(default=None, description="Optional bot id to clone config_json from.")


class TemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    preview_topics: List[str]


class BotPatch(BaseModel):
    name: Optional[str] = None
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None
    slug: Optional[str] = None


class BotResponse(BaseModel):
    id: str
    slug: str
    name: str
    is_active: bool
    is_deleted: bool
    config_schema_version: int
    config_version: int
    tags: List[str] = []
    is_default: bool = False
    default_renamed: bool = False
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None


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
        tags=list(bot.tags or []),
        is_default=bot.is_default,
        default_renamed=bot.default_renamed,
        created_at=bot.created_at,
        updated_at=bot.updated_at,
        deleted_at=bot.deleted_at,
    )


def _resolve_bot(slug: str) -> BotModel:
    bot = Bots.get_by_slug(slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    return bot


def _require_membership(admin: AdminUserModel, bot_id: str, *, allowed_roles: tuple) -> None:
    """DB-checked membership for destructive routes. Phase 4 contract:
    don't trust the JWT's denormalized ``bot_ids`` claim for writes.
    Honours ``is_superadmin`` via the canonical helpers in ``services.authz``.

    ``allowed_roles`` is required — there is no sensible default for a
    write gate, and the prior default of ``ROLES`` was misleading (it
    fell through to ``require_writer`` and silently rejected viewers).
    """
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
    # C2: workspace-level permission to create bots (BOT_CREATION).
    require_bot_creator(admin)
    if not _SLUG_RE.match(payload.slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slug must be lowercase alphanumeric with optional hyphens (2-64 chars)",
        )
    if Bots.get_by_slug(payload.slug):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already in use")

    # When duplicating, deep-copy the source bot's config_json. The
    # caller must have member access to the source so duplicate can't be
    # used as a privilege escalation to read configs the admin couldn't
    # otherwise see.
    config_json: Optional[dict] = None
    template_id = payload.template_id or "blank"
    if payload.source_bot_id:
        source = Bots.get_by_id(payload.source_bot_id)
        if source is None or source.is_deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source bot not found")
        require_member(admin, source.id)
        # Stamp the duplicate with its own slug/name so prompt templates
        # referencing the original don't leak the source's identity.
        config_json = dict(source.config_json or {})
        agent_cfg = config_json.get("agent_config") or {}
        if isinstance(agent_cfg, dict):
            agent_cfg = dict(agent_cfg)
            agent_cfg["bot_slug"] = payload.slug
            agent_cfg["bot_name"] = payload.name
            config_json["agent_config"] = agent_cfg
    elif template_id.startswith("workspace:"):
        # Admin-saved workspace template. The id format is
        # ``workspace:<workspace_bot_templates.id>``.
        from models.workspace_bot_templates import WorkspaceBotTemplates
        wt_id = template_id.split(":", 1)[1]
        tpl = WorkspaceBotTemplates.get_by_id(wt_id)
        if tpl is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown workspace template id: {wt_id}",
            )
        config_json = dict(tpl.config_json or {})
        agent_cfg = config_json.get("agent_config") or {}
        if isinstance(agent_cfg, dict):
            agent_cfg = dict(agent_cfg)
            agent_cfg["bot_slug"] = payload.slug
            agent_cfg["bot_name"] = payload.name
            config_json["agent_config"] = agent_cfg
    else:
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
        "Admin %s created bot id=%s slug=%s from %s",
        admin.id,
        bot_id,
        payload.slug,
        f"source={payload.source_bot_id}" if payload.source_bot_id else f"template={template_id}",
    )
    audit(
        "bot.created",
        admin_id=admin.id,
        bot_id=bot_id,
        target_type="bot",
        target_id=bot_id,
        payload={
            "slug": payload.slug,
            "source_bot_id": payload.source_bot_id,
            "template_id": template_id,
        },
    )
    return _bot_to_response(bot)


class SlugAvailability(BaseModel):
    slug: str
    available: bool
    reason: Optional[str] = None


@router.get("/bots/slug-available", response_model=SlugAvailability)
def check_slug_available(
    slug: str,
    _admin: AdminUserModel = Depends(get_admin),
):
    """Cheap pre-flight for the create wizard's inline validator.

    Returns ``{available: false}`` if the slug fails the regex or is
    already in use (including soft-deleted bots, since their slugs are
    still reserved until the purge job runs). Membership is intentionally
    not checked: any authenticated admin can ask whether a slug is free.
    """
    slug = (slug or "").strip().lower()
    if not _SLUG_RE.match(slug):
        return SlugAvailability(slug=slug, available=False, reason="invalid")
    if Bots.get_by_slug(slug) is not None:
        return SlugAvailability(slug=slug, available=False, reason="taken")
    return SlugAvailability(slug=slug, available=True)


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
    tags: List[str] = []
    # Per-bot knowledge usage so /bots can render Files + Storage columns
    # without N+1 fetching per-bot file lists. Aggregated from
    # ``Emly_Files.storage_summary``.
    file_count: int = 0
    storage_bytes: int = 0
    updated_at: datetime


# Cached compute window. The summary endpoint is hit on every page load
# of /bots; without a cache the per-bot aggregations dominate Postgres
# CPU on multi-bot workspaces. 60s is acceptable freshness for a dense
# overview table. Each entry also records which bots it covers, and is
# only reused while that set is unchanged, so a create / delete / restore
# / membership change shows up immediately on every worker.
_SUMMARY_CACHE: dict[str, tuple[float, tuple[str, ...], list[WorkspaceBotSummary]]] = {}
_SUMMARY_TTL = 60.0


def _live_bot_ids_for(admin_id: str) -> tuple[str, ...]:
    from models.bots import Bot

    ids = [m.bot_id for m in AdminBotMemberships.list_for_admin(admin_id)]
    if not ids:
        return ()
    rows = Bot.select(Bot.id).where(
        Bot.id.in_(ids) & (Bot.is_deleted == False)  # noqa: E712
    )
    return tuple(sorted(r.id for r in rows))


@router.get("/bots/summary", response_model=List[WorkspaceBotSummary])
def list_bots_summary(admin: AdminUserModel = Depends(get_admin)):
    """Per-bot aggregates for the workspace overview table.

    Phase 5 of the backend-backfill plan. Iterates the admin's bot
    memberships and computes a 24h aggregate from each bot's report.
    Replaces the UI's prior N+1 over `/dashboard/stats`.
    """
    import time as _time

    cache_key = admin.id
    live_ids = _live_bot_ids_for(admin.id)
    cached = _SUMMARY_CACHE.get(cache_key)
    if cached and (_time.time() - cached[0]) < _SUMMARY_TTL and cached[1] == live_ids:
        return cached[2]

    from datetime import timedelta

    from models.bot_channels import BotChannels
    from models.emly_files import Emly_Files
    from models.emly_messages import EMLYMessages
    from models.emly_sessions import EMLYSessions

    summaries: List[WorkspaceBotSummary] = []
    now = datetime.now()
    since = now - timedelta(hours=24)
    # ``live_ids`` is already this admin's non-deleted bots; reuse it rather
    # than re-reading the memberships.
    for bot_id in live_ids:
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
            sessions_24h = EMLYSessions.count_for_bot(bot.id, started_after=since)
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
        # Knowledge usage. Single aggregate query per bot — cheap, but
        # cached for 60s alongside everything else in the summary.
        try:
            file_count, storage_bytes = Emly_Files.storage_summary(bot.id)
        except Exception:
            log.exception("storage_summary failed for bot=%s", bot.id)
            file_count, storage_bytes = 0, 0
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
                tags=list(bot.tags or []),
                file_count=file_count,
                storage_bytes=storage_bytes,
                updated_at=bot.updated_at,
            )
        )
    _SUMMARY_CACHE[cache_key] = (_time.time(), live_ids, summaries)
    return summaries


# ---------------------------------------------------------------------------
# Validation summary — per-bot {errors, warnings} so the workspace overview
# can show a health icon without doing N+1 ``GET /config`` round-trips.
# ---------------------------------------------------------------------------
class BotValidationCounts(BaseModel):
    slug: str
    errors: int
    warnings: int
    info: int


# Cache keyed by (bot_id, config_version). Invalidates implicitly on any
# save because save_config_for_bot bumps the version. No TTL: a stale
# entry is only stale across an unrelated boot.
_VALIDATION_CACHE: dict[tuple[str, int], BotValidationCounts] = {}


def invalidate_validation_cache(bot_id: str) -> None:
    """Drop every entry for this bot. Called from save / publish so the
    next read recomputes against the new config."""
    stale = [k for k in _VALIDATION_CACHE if k[0] == bot_id]
    for k in stale:
        _VALIDATION_CACHE.pop(k, None)


@router.get("/bots/validation-summary", response_model=List[BotValidationCounts])
def list_bots_validation_summary(admin: AdminUserModel = Depends(get_admin)):
    """One row per readable bot with error / warning / info counts.

    Replaces the per-row ``GET /config`` + client-side ``validateConfig``
    fan-out in ``ui/app/bots/page.tsx`` (one round-trip vs. N). Results
    are cached by ``(bot_id, config_version)`` so the only DB work after
    the first hit is the membership lookup.
    """
    from routes.admin_bot_dashboard import _build_validation_context
    from services.config_validation import count_by_severity, validate_config

    if admin.is_superadmin:
        bots = Bots.list_active()
    else:
        memberships = AdminBotMemberships.list_for_admin(admin.id)
        bot_ids = [m.bot_id for m in memberships]
        bots = [b for b in (Bots.get_by_id(bid) for bid in bot_ids) if b is not None and not b.is_deleted]

    out: List[BotValidationCounts] = []
    for bot in bots:
        key = (bot.id, bot.config_version)
        cached = _VALIDATION_CACHE.get(key)
        if cached is not None:
            out.append(cached)
            continue
        try:
            ctx = _build_validation_context(bot)
            issues = validate_config(bot.config_json or {}, ctx)
            counts = count_by_severity(issues)
            row = BotValidationCounts(
                slug=bot.slug,
                errors=counts["error"],
                warnings=counts["warning"],
                info=counts["info"],
            )
        except Exception:
            log.exception("validation summary failed for bot=%s", bot.id)
            # Surface zeros rather than dropping the row — the overview
            # icon disappears for that bot instead of breaking the table.
            row = BotValidationCounts(slug=bot.slug, errors=0, warnings=0, info=0)
        _VALIDATION_CACHE[key] = row
        out.append(row)
    return out


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

    # Default-space rename-lock. The auto-created default workspace gets
    # exactly one rename (of either name or slug); after that, the field
    # is locked so invite emails and bookmarks stay valid. Other spaces
    # have unlimited renames as before.
    requested_rename = (
        (payload.name is not None and payload.name != bot.name)
        or (payload.slug is not None and payload.slug != bot.slug)
    )
    if bot.is_default and bot.default_renamed and requested_rename:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "default_space_rename_locked"},
        )

    next_slug: Optional[str] = None
    if payload.slug is not None and payload.slug != bot.slug:
        candidate = payload.slug.strip().lower()
        if not _SLUG_RE.match(candidate):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slug must be lowercase alphanumeric with optional hyphens (2-64 chars)",
            )
        if Bots.get_by_slug(candidate) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already in use")
        if not Bots.update_slug(bot.id, candidate):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already in use")
        log.info("Admin %s renamed bot=%s slug %s -> %s", admin.id, bot.id, bot.slug, candidate)
        next_slug = candidate
        _SUMMARY_CACHE.clear()
    if payload.name is not None:
        from models.bots import Bot
        Bot.update(name=payload.name, updated_at=datetime.utcnow()).where(Bot.id == bot.id).execute()

    # Burn the one-shot rename flag on the default space if any rename
    # actually happened. Done last so we only flip on success.
    if bot.is_default and not bot.default_renamed and requested_rename:
        Bots.mark_default_renamed(bot.id)
        try:
            audit(
                "bot.default_renamed",
                admin_id=admin.id,
                bot_id=bot.id,
                target_type="bot",
                target_id=bot.id,
                payload={
                    "from_name": bot.name,
                    "to_name": payload.name,
                    "from_slug": bot.slug,
                    "to_slug": payload.slug,
                },
            )
        except Exception:
            log.exception("audit default-rename failed")
    if payload.tags is not None:
        Bots.update_tags(bot.id, payload.tags)
        # Tag changes affect the workspace summary; nuke the cache so
        # subsequent /bots loads see fresh chips immediately.
        _SUMMARY_CACHE.clear()
    if payload.is_active is not None and payload.is_active != bot.is_active:
        Bots.set_active(bot.id, payload.is_active)
        log.info(
            "Admin %s %s bot=%s",
            admin.id,
            "unpaused" if payload.is_active else "paused",
            bot.id,
        )
        _SUMMARY_CACHE.clear()
        try:
            from utils.dependencies import AGENT_SERVICE_INSTANCE
            AGENT_SERVICE_INSTANCE.invalidate_bot(bot.id)
        except Exception:
            log.exception("invalidate_bot failed after pause toggle bot=%s", bot.id)
    refreshed_slug = next_slug or bot.slug
    audit(
        "bot.patched",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="bot",
        target_id=bot.id,
        payload={
            "slug": payload.slug,
            "name": payload.name,
            "tags": payload.tags,
            "is_active": payload.is_active,
        },
    )
    return _bot_to_response(_resolve_bot(refreshed_slug))


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
    audit(
        "bot.soft_deleted",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="bot",
        target_id=bot.id,
        payload={"slug": bot.slug},
    )
    _SUMMARY_CACHE.clear()

    # Best-effort runtime invalidation; the registry will stop serving
    # this bot on its next ``get_handler`` call regardless.
    try:
        from utils.dependencies import AGENT_SERVICE_INSTANCE
        AGENT_SERVICE_INSTANCE.invalidate_bot(bot.id)
    except Exception:
        log.exception("invalidate_bot after soft-delete failed for bot=%s", bot.id)

    return {"status": "scheduled_for_purge", "bot_id": bot.id}


class DeletedBotResponse(BaseModel):
    """Row of the recently-deleted list. Slug is the most useful key for
    the restore button; the daily purge will hard-delete after the grace
    window so ``hard_purge_at`` is what the UI displays as a countdown."""
    id: str
    slug: str
    name: str
    deleted_at: datetime
    hard_purge_at: datetime


@router.get("/bots-deleted", response_model=List[DeletedBotResponse])
def list_deleted_bots(admin: AdminUserModel = Depends(get_admin)):
    """List soft-deleted bots the admin has owner membership on.

    Path is intentionally ``/bots-deleted`` (not ``/bots/deleted``) so
    the ``{slug}`` route doesn't shadow it.
    """
    from services.bot_purge import PURGE_GRACE

    if admin.is_superadmin:
        rows = Bots.list_deleted()
    else:
        memberships = AdminBotMemberships.list_for_admin(admin.id)
        wanted = {m.bot_id for m in memberships if m.role == "owner"}
        rows = [b for b in Bots.list_deleted() if b.id in wanted]
    out: List[DeletedBotResponse] = []
    for b in rows:
        if b.deleted_at is None:
            continue
        out.append(
            DeletedBotResponse(
                id=b.id,
                slug=b.slug,
                name=b.name,
                deleted_at=b.deleted_at,
                hard_purge_at=b.deleted_at + PURGE_GRACE,
            )
        )
    return out


@router.post("/bots/{slug}/restore", response_model=BotResponse)
def restore_bot(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    """Reverse a soft-delete while the bot is still within the purge grace
    window. Owner-only. Returns 404 once the daily purge job has run.
    """
    bot = Bots.get_by_slug(slug)
    if bot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    if not bot.is_deleted:
        # Idempotent: already-live is a no-op success.
        return _bot_to_response(bot)
    require_owner(admin, bot.id)
    Bots.restore(bot.id)
    log.info("Admin %s restored bot=%s", admin.id, bot.id)
    audit(
        "bot.restored",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="bot",
        target_id=bot.id,
        payload={"slug": bot.slug},
    )
    _SUMMARY_CACHE.clear()
    try:
        from utils.dependencies import AGENT_SERVICE_INSTANCE
        AGENT_SERVICE_INSTANCE.invalidate_bot(bot.id)
    except Exception:
        log.exception("invalidate_bot after restore failed for bot=%s", bot.id)
    refreshed = Bots.get_by_id(bot.id)
    return _bot_to_response(refreshed) if refreshed else _bot_to_response(bot)


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
@router.get("/bots/{slug}/admins", response_model=List[MembershipResponse])
def list_bot_admins(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    require_member(admin, bot.id)
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

    from peewee import IntegrityError
    try:
        m = AdminBotMemberships.grant(
            id=f"mbr-{uuid.uuid4()}",
            admin_id=payload.admin_id,
            bot_id=bot.id,
            role=payload.role,
        )
    except IntegrityError:
        # Lost a race with a concurrent grant; surface a clean 409
        # instead of letting the unique-index violation become a 500.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Membership already exists")
    audit(
        "membership.granted",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="admin",
        target_id=payload.admin_id,
        payload={"role": payload.role},
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
    audit(
        "membership.role_updated",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="admin",
        target_id=admin_id,
        payload={"from_role": existing.role, "to_role": payload.role},
    )
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
    audit(
        "membership.revoked",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="admin",
        target_id=admin_id,
        payload={"role": existing.role},
    )
