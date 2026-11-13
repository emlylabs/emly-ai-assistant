"""Workspace-scoped admin routes.

§6 of ``improve-ux.md``: cross-bot operations. This module exposes:

- ``GET/PUT /api/admin/workspace/settings`` — workspace defaults
  (theme tokens, default LLM, default limits). Read open to any
  authenticated admin; write requires superadmin.
- ``GET/POST/DELETE /api/admin/workspace/templates`` — admin-saved
  bot templates that the create wizard lists alongside the code-baked
  starter templates.
- ``POST /api/admin/bots/bulk-action`` — apply pause/unpause/delete/tag
  to a slug list in one round-trip. Each row is processed independently
  so a single 403 doesn't abort the batch.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from models.admin_bot_memberships import AdminBotMemberships
from models.admin_users import AdminUserModel
from models.bots import Bots
from models.workspace_bot_templates import WorkspaceBotTemplates
from models.workspace_settings import WorkspaceSettingsStore
from services.audit import audit
from services.auth.dependencies import get_admin
from services.authz import require_superadmin, require_writer, require_owner

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Workspace settings
# ---------------------------------------------------------------------------


_WORKSPACE_SETTINGS_MAX_BYTES = 64 * 1024  # 64 KB serialized cap
_WORKSPACE_SETTINGS_MAX_KEYS = 256
_WORKSPACE_SETTINGS_KEY_RE = __import__("re").compile(r"^[a-zA-Z0-9_.\-]{1,64}$")


def _validate_workspace_settings(settings: Dict[str, Any]) -> None:
    """Defense-in-depth checks before persisting the blob.

    Stops a malformed PUT from poisoning shared workspace state (which
    every editor reads as "ghost text" defaults). Doesn't enforce a
    typed shape — we still let the UI evolve its key set — but it does
    cap size, key count, key shape, and per-value primitive depth so
    one bad write can't break every other admin's editor.
    """
    import json

    if not isinstance(settings, dict):
        raise HTTPException(status_code=400, detail={"code": "invalid_settings"})
    if len(settings) > _WORKSPACE_SETTINGS_MAX_KEYS:
        raise HTTPException(
            status_code=400,
            detail={"code": "too_many_keys", "limit": _WORKSPACE_SETTINGS_MAX_KEYS},
        )
    for key in settings.keys():
        if not isinstance(key, str) or not _WORKSPACE_SETTINGS_KEY_RE.match(key):
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_key", "key": str(key)[:64]},
            )
    from services.otp import (
        MAX_EXPIRES_IN_MINUTES,
        MIN_EXPIRES_IN_MINUTES,
        WORKSPACE_EXPIRY_KEY,
        valid_expiry_minutes,
    )

    otp_expiry = settings.get(WORKSPACE_EXPIRY_KEY)
    if otp_expiry is not None and not valid_expiry_minutes(otp_expiry):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_value",
                "key": WORKSPACE_EXPIRY_KEY,
                "message": (
                    f"Must be a whole number of minutes from {MIN_EXPIRES_IN_MINUTES} "
                    f"to {MAX_EXPIRES_IN_MINUTES}."
                ),
            },
        )
    try:
        encoded = json.dumps(settings, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "non_serializable", "message": str(exc)},
        )
    if len(encoded.encode("utf-8")) > _WORKSPACE_SETTINGS_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "settings_too_large", "limit_bytes": _WORKSPACE_SETTINGS_MAX_BYTES},
        )


class WorkspaceSettingsPayload(BaseModel):
    """Free-form key-value blob. The schema is intentionally loose: the UI
    decides what keys mean; the backend just persists. As patterns emerge
    we can graduate well-known keys to typed fields. The settings dict is
    further validated in the route handler against size / key-shape caps."""

    settings: Dict[str, Any] = Field(default_factory=dict)


class WorkspaceSettingsResponse(BaseModel):
    settings: Dict[str, Any]
    updated_at: str
    updated_by_admin_id: Optional[str] = None


@router.get("/workspace/settings", response_model=WorkspaceSettingsResponse)
def get_workspace_settings(_admin: AdminUserModel = Depends(get_admin)):
    """Any admin can read workspace defaults. Surfacing inline in the
    per-bot editor as "Using workspace default" needs every editor session
    to have read access."""
    s = WorkspaceSettingsStore.get()
    return WorkspaceSettingsResponse(
        settings=s.settings,
        updated_at=s.updated_at.isoformat(),
        updated_by_admin_id=s.updated_by_admin_id,
    )


@router.put("/workspace/settings", response_model=WorkspaceSettingsResponse)
def put_workspace_settings(
    payload: WorkspaceSettingsPayload,
    admin: AdminUserModel = Depends(get_admin),
):
    """Replace workspace defaults wholesale. Superadmin-only — these
    settings affect every new bot and every editor's "ghost text"
    indicator. Audit trail lives in ``updated_by_admin_id``."""
    require_superadmin(admin)
    _validate_workspace_settings(payload.settings)
    s = WorkspaceSettingsStore.put(payload.settings, admin_id=admin.id)
    log.info("workspace.settings.updated by=%s keys=%s", admin.id, sorted(payload.settings.keys()))
    audit(
        "workspace.settings_updated",
        admin_id=admin.id,
        target_type="workspace",
        payload={"keys": sorted(payload.settings.keys())},
    )
    return WorkspaceSettingsResponse(
        settings=s.settings,
        updated_at=s.updated_at.isoformat(),
        updated_by_admin_id=s.updated_by_admin_id,
    )


# ---------------------------------------------------------------------------
# Workspace bot templates
# ---------------------------------------------------------------------------


class WorkspaceTemplateResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    created_by_admin_id: Optional[str] = None
    created_at: str
    updated_at: str


class WorkspaceTemplateDetail(WorkspaceTemplateResponse):
    config_json: Dict[str, Any]


class CreateWorkspaceTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=512)
    # Caller can either snapshot from an existing bot they're a member of,
    # or pass the config blob directly (used by the "save as workspace
    # template" button inside the config editor).
    source_bot_slug: Optional[str] = None
    config_json: Optional[Dict[str, Any]] = None


@router.get("/workspace/templates", response_model=List[WorkspaceTemplateResponse])
def list_workspace_templates(_admin: AdminUserModel = Depends(get_admin)):
    rows = WorkspaceBotTemplates.list()
    return [
        WorkspaceTemplateResponse(
            id=r.id,
            name=r.name,
            description=r.description,
            created_by_admin_id=r.created_by_admin_id,
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in rows
    ]


@router.get("/workspace/templates/{template_id}", response_model=WorkspaceTemplateDetail)
def get_workspace_template(template_id: str, _admin: AdminUserModel = Depends(get_admin)):
    row = WorkspaceBotTemplates.get_by_id(template_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return WorkspaceTemplateDetail(
        id=row.id,
        name=row.name,
        description=row.description,
        config_json=row.config_json,
        created_by_admin_id=row.created_by_admin_id,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


@router.post(
    "/workspace/templates",
    response_model=WorkspaceTemplateDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace_template(
    payload: CreateWorkspaceTemplateRequest,
    admin: AdminUserModel = Depends(get_admin),
):
    """Either snapshot from a source bot (caller must be a member) or
    accept an explicit ``config_json``. Both paths land in the same
    table; the source bot is just a convenience."""
    config = payload.config_json
    if payload.source_bot_slug:
        bot = Bots.get_by_slug(payload.source_bot_slug)
        if bot is None or bot.is_deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source bot not found")
        require_writer(admin, bot.id)
        # Snapshot the source's config. Drop slug/name overrides so the
        # next bot created from this template doesn't inherit identity
        # tags from the source.
        snapshot = dict(bot.config_json or {})
        agent_cfg = snapshot.get("agent_config")
        if isinstance(agent_cfg, dict):
            agent_cfg = dict(agent_cfg)
            agent_cfg.pop("bot_slug", None)
            agent_cfg.pop("bot_name", None)
            snapshot["agent_config"] = agent_cfg
        config = snapshot
    if not isinstance(config, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either source_bot_slug or config_json is required",
        )
    template_id = f"wt-{uuid.uuid4()}"
    row = WorkspaceBotTemplates.insert(
        id=template_id,
        name=payload.name,
        description=payload.description,
        config_json=config,
        created_by_admin_id=admin.id,
    )
    log.info("workspace.template.created by=%s template=%s name=%r", admin.id, template_id, payload.name)
    audit(
        "workspace.template_created",
        admin_id=admin.id,
        target_type="workspace_template",
        target_id=template_id,
        payload={"name": payload.name, "source_bot_slug": payload.source_bot_slug},
    )
    return WorkspaceTemplateDetail(
        id=row.id,
        name=row.name,
        description=row.description,
        config_json=row.config_json,
        created_by_admin_id=row.created_by_admin_id,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


@router.delete("/workspace/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace_template(
    template_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    require_superadmin(admin)
    if not WorkspaceBotTemplates.delete(template_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    log.info("workspace.template.deleted by=%s template=%s", admin.id, template_id)
    audit(
        "workspace.template_deleted",
        admin_id=admin.id,
        target_type="workspace_template",
        target_id=template_id,
    )


# ---------------------------------------------------------------------------
# Bulk actions on bots.
#
# The /bots table's row-selection feature commits via this endpoint. Each
# slug is processed independently so a single 403 / 409 doesn't abort the
# whole batch — the response surfaces per-row status so the UI can show
# partial success.
# ---------------------------------------------------------------------------


BulkActionName = Literal["pause", "unpause", "delete", "tag-add", "tag-remove"]


class BulkBotActionRequest(BaseModel):
    action: BulkActionName
    slugs: List[str] = Field(min_length=1, max_length=200)
    # Used by tag-add / tag-remove only.
    tag: Optional[str] = None


class BulkBotActionResult(BaseModel):
    slug: str
    status: str  # "ok" | "skipped" | "error"
    detail: Optional[str] = None
    code: Optional[str] = None  # structured error code for "error" rows


class BulkBotActionResponse(BaseModel):
    results: List[BulkBotActionResult]


@router.post("/bots/bulk-action", response_model=BulkBotActionResponse)
def bulk_bot_action(
    payload: BulkBotActionRequest,
    admin: AdminUserModel = Depends(get_admin),
):
    """Apply ``action`` to every slug. Per-bot authz is re-checked
    (writer for pause/unpause/tag; owner for delete) so an admin
    selecting bots they don't own gets per-row errors rather than the
    batch silently mutating things they aren't allowed to touch."""
    if payload.action in ("tag-add", "tag-remove") and not (payload.tag or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tag is required for tag actions")
    results: List[BulkBotActionResult] = []
    for slug in payload.slugs:
        bot = Bots.get_by_slug(slug)
        if bot is None or bot.is_deleted:
            results.append(BulkBotActionResult(slug=slug, status="error", detail="not_found"))
            continue
        try:
            if payload.action == "delete":
                require_owner(admin, bot.id)
                Bots.soft_delete(bot.id)
                results.append(BulkBotActionResult(slug=slug, status="ok"))
                try:
                    from utils.dependencies import AGENT_SERVICE_INSTANCE
                    AGENT_SERVICE_INSTANCE.invalidate_bot(bot.id)
                except Exception:
                    log.exception("invalidate_bot after bulk delete bot=%s", bot.id)
                continue
            require_writer(admin, bot.id)
            if payload.action == "pause":
                if not bot.is_active:
                    results.append(BulkBotActionResult(slug=slug, status="skipped", detail="already_paused"))
                    continue
                Bots.set_active(bot.id, False)
                results.append(BulkBotActionResult(slug=slug, status="ok"))
            elif payload.action == "unpause":
                if bot.is_active:
                    results.append(BulkBotActionResult(slug=slug, status="skipped", detail="already_live"))
                    continue
                Bots.set_active(bot.id, True)
                results.append(BulkBotActionResult(slug=slug, status="ok"))
            elif payload.action in ("tag-add", "tag-remove"):
                current = list(bot.tags or [])
                changed = False
                if payload.action == "tag-add" and payload.tag not in current:
                    current.append(payload.tag)
                    changed = True
                elif payload.action == "tag-remove" and payload.tag in current:
                    current = [t for t in current if t != payload.tag]
                    changed = True
                if changed:
                    Bots.update_tags(bot.id, current)
                    results.append(BulkBotActionResult(slug=slug, status="ok"))
                else:
                    results.append(BulkBotActionResult(slug=slug, status="skipped", detail="no_change"))
        except HTTPException as exc:
            # ``exc.detail`` is typically the structured ``{"code": ...}``
            # dict that ``services.authz`` raises. Preserve the code field
            # so the UI can render structured errors instead of parsing a
            # Python-repr blob.
            detail = exc.detail
            code: Optional[str] = None
            if isinstance(detail, dict):
                code = detail.get("code")
                detail_str = code or str(detail)
            else:
                detail_str = str(detail)
            results.append(
                BulkBotActionResult(
                    slug=slug,
                    status="error",
                    detail=detail_str[:120],
                    code=code,
                )
            )
        except Exception as exc:
            log.exception("bulk bot action failed slug=%s", slug)
            results.append(
                BulkBotActionResult(
                    slug=slug,
                    status="error",
                    detail=str(exc)[:120],
                    code="internal_error",
                )
            )
    log.info(
        "bots.bulk_action by=%s action=%s slugs=%d tag=%s",
        admin.id,
        payload.action,
        len(payload.slugs),
        payload.tag,
    )
    audit(
        "bots.bulk_action",
        admin_id=admin.id,
        target_type="bot",
        payload={
            "action": payload.action,
            "tag": payload.tag,
            "results": [
                {"slug": r.slug, "status": r.status, "detail": r.detail}
                for r in results
            ],
        },
    )
    return BulkBotActionResponse(results=results)


# ---------------------------------------------------------------------------
# Workspace-wide crawl monitor (§11 T7)
# ---------------------------------------------------------------------------


class WorkspaceCrawlJobRow(BaseModel):
    """One row in the workspace-wide crawl listing. Adds ``bot_slug`` +
    ``bot_name`` so the UI can render the bot without an N+1 lookup."""

    id: str
    bot_id: str
    bot_slug: str
    bot_name: str
    seed_url: str
    status: str
    pages_total: int
    pages_done: int
    pages_skipped: int
    pages_failed: int
    document_type: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None


class WorkspaceCrawlJobsResponse(BaseModel):
    items: List[WorkspaceCrawlJobRow]
    total: int
    limit: int
    offset: int


@router.get("/crawl-jobs", response_model=WorkspaceCrawlJobsResponse)
def list_workspace_crawl_jobs(
    limit: int = 50,
    offset: int = 0,
    admin: AdminUserModel = Depends(get_admin),
):
    """Every recent crawl across every bot the caller can read.

    Superadmins see all active bots; everyone else is scoped to their
    membership list. The endpoint is read-only — pause/resume/cancel
    still happen per-bot to keep the existing audit-trail and authz
    paths intact.
    """
    from models.crawl_jobs import Crawl_Jobs

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    if admin.is_superadmin:
        bots = [b for b in Bots.list_active() if not b.is_deleted]
    else:
        memberships = AdminBotMemberships.list_for_admin(admin.id)
        bots = [
            b
            for b in (Bots.get_by_id(m.bot_id) for m in memberships)
            if b is not None and not b.is_deleted
        ]
    bot_ids = [b.id for b in bots]
    by_id = {b.id: b for b in bots}

    jobs = Crawl_Jobs.list_for_bots(bot_ids, limit=limit, offset=offset)
    total = Crawl_Jobs.count_for_bots(bot_ids)
    items = [
        WorkspaceCrawlJobRow(
            id=j["id"],
            bot_id=j["bot_id"],
            bot_slug=by_id[j["bot_id"]].slug if j["bot_id"] in by_id else "",
            bot_name=by_id[j["bot_id"]].name if j["bot_id"] in by_id else "",
            seed_url=j["seed_url"],
            status=j["status"],
            pages_total=j["pages_total"],
            pages_done=j["pages_done"],
            pages_skipped=j["pages_skipped"],
            pages_failed=j["pages_failed"],
            document_type=j["document_type"],
            created_at=j.get("created_at"),
            updated_at=j.get("updated_at"),
            completed_at=j.get("completed_at"),
        )
        for j in jobs
        if j["bot_id"] in by_id
    ]
    return WorkspaceCrawlJobsResponse(items=items, total=total, limit=limit, offset=offset)
