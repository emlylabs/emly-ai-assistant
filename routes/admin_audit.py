"""Phase 5 backend-backfill — read-only audit log endpoint.

The ``admin_audit_log`` table is populated by ``services/audit.audit()``
on every authn/authz event and admin/bot mutation, and has been writable
since migration 006. This file finally surfaces it through HTTP so the
admin UI can render the audit page (Phase 11).

Auth model:
    - Superadmins see the global feed; ``bot_id`` may be omitted.
    - Bot-scoped admins must pass a ``bot_id`` that they're a member of;
      cross-bot reads are 403.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from models.admin_audit_log import AdminAuditLogModel, AdminAuditLogs
from models.admin_bot_memberships import AdminBotMemberships
from models.admin_users import AdminUserModel
from models.bots import Bots
from services.auth.dependencies import get_admin

log = logging.getLogger(__name__)
router = APIRouter()


class AuditListResponse(BaseModel):
    items: List[AdminAuditLogModel]
    skip: int
    limit: int


@router.get("/audit-logs", response_model=AuditListResponse)
def list_audit_logs(
    admin_id: Optional[str] = Query(None),
    bot_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    admin: AdminUserModel = Depends(get_admin),
):
    """Workspace-wide audit feed. Superadmin-only when bot_id is omitted."""
    if not admin.is_superadmin:
        if not bot_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="bot_id required for non-superadmin reads",
            )
        if AdminBotMemberships.get(admin.id, bot_id) is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized for this bot",
            )
    items = AdminAuditLogs.list_filtered(
        admin_id=admin_id,
        bot_id=bot_id,
        action=action,
        success=success,
        skip=skip,
        limit=limit,
    )
    return AuditListResponse(items=items, skip=skip, limit=limit)


@router.get("/bots/{slug}/audit-logs", response_model=AuditListResponse)
def list_bot_audit_logs(
    slug: str,
    action: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    admin: AdminUserModel = Depends(get_admin),
):
    """Bot-scoped audit feed. Any role on the bot can read."""
    bot = Bots.get_by_slug(slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    if AdminBotMemberships.get(admin.id, bot.id) is None and not admin.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this bot")
    items = AdminAuditLogs.list_filtered(
        bot_id=bot.id,
        action=action,
        success=success,
        skip=skip,
        limit=limit,
    )
    return AuditListResponse(items=items, skip=skip, limit=limit)
