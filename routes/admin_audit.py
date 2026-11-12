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
from services.authz import require_member, require_owner, require_superadmin
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
        require_member(admin, bot_id)
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
    require_member(admin, bot.id)
    items = AdminAuditLogs.list_filtered(
        bot_id=bot.id,
        action=action,
        success=success,
        skip=skip,
        limit=limit,
    )
    return AuditListResponse(items=items, skip=skip, limit=limit)


# ---------------------------------------------------------------------------
# CSV export (C7)
# ---------------------------------------------------------------------------
import csv  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Iterable, Iterator  # noqa: E402

from fastapi.responses import StreamingResponse  # noqa: E402

CSV_COLUMNS = (
    "created_at", "action", "success", "admin_id", "admin_email", "bot_id",
    "target_type", "target_id", "ip", "user_agent", "payload",
)
# Cells starting with these are treated as formulas by spreadsheet apps.
# Audit rows hold attacker-influenced text (user agents, emails), so neutralise
# them with a leading apostrophe (OWASP "CSV injection").
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.isoformat()
    elif isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, default=str)
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        text = "'" + text
    return text


def _csv_stream(rows: Iterable[AdminAuditLogModel]) -> Iterator[str]:
    from models.admin_users import AdminUsers

    emails: dict[str, Optional[str]] = {}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        if row.admin_id and row.admin_id not in emails:
            user = AdminUsers.get_by_id_including_deleted(row.admin_id)
            emails[row.admin_id] = user.email if user else None
        writer.writerow([_csv_cell(v) for v in (
            row.created_at, row.action, row.success, row.admin_id, emails.get(row.admin_id or ""),
            row.bot_id, row.target_type, row.target_id, row.ip, row.ua, row.payload,
        )])
        if buf.tell() > 64 * 1024:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
    yield buf.getvalue()


def _csv_response(rows: Iterable[AdminAuditLogModel], scope: str) -> StreamingResponse:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(
        _csv_stream(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="audit-{scope}-{stamp}.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/audit/export")
def export_audit_logs(
    admin_id: Optional[str] = Query(None),
    bot_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    since: Optional[datetime] = Query(None, description="Inclusive lower bound (ISO 8601)."),
    until: Optional[datetime] = Query(None, description="Exclusive upper bound (ISO 8601)."),
    admin: AdminUserModel = Depends(get_admin),
):
    """Workspace-wide audit log as CSV. Superadmins only. The export itself
    is audited (``audit.exported``)."""
    require_superadmin(admin)
    filters = {"admin_id": admin_id, "bot_id": bot_id, "action": action, "success": success,
               "since": since, "until": until}
    from services.audit import audit

    audit("audit.exported", admin_id=admin.id, bot_id=bot_id, target_type="audit_log",
          payload={k: (v.isoformat() if isinstance(v, datetime) else v)
                   for k, v in filters.items() if v is not None})
    return _csv_response(AdminAuditLogs.iter_filtered(**filters), "workspace")


@router.get("/bots/{slug}/audit/export")
def export_bot_audit_logs(
    slug: str,
    action: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    admin: AdminUserModel = Depends(get_admin),
):
    """One bot's audit log as CSV. Owners of the bot (and superadmins)."""
    bot = Bots.get_by_slug(slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    require_owner(admin, bot.id)
    filters = {"action": action, "success": success, "since": since, "until": until}
    from services.audit import audit

    audit("audit.exported", admin_id=admin.id, bot_id=bot.id, target_type="audit_log",
          payload={k: (v.isoformat() if isinstance(v, datetime) else v)
                   for k, v in filters.items() if v is not None})
    return _csv_response(AdminAuditLogs.iter_filtered(bot_id=bot.id, **filters), bot.slug)
