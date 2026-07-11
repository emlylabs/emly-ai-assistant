"""Audit-log writer.

Single entry point for recording authentication, authorization, and
admin/bot lifecycle events. Writes are best-effort — a failure to record
the event must not prevent the underlying request from completing
(otherwise a DB blip becomes a denial-of-service for everything).

Use:

    from services.audit import audit
    audit("auth.login", admin_id=admin.id, ip=request.client.host, success=True)

Conventions for ``action``:
    auth.*           — authentication: login, logout, token_invalid, …
    authz.*          — authorization: denied, superadmin_bypass
    csrf.*           — CSRF middleware decisions
    admin.*          — admin lifecycle (created, deactivated, deleted, relinked)
    bot.*            — bot lifecycle (created, config_updated, deleted)
    membership.*     — bot-membership grants/revokes/role-changes
    pending.*        — pending_admin lifecycle
    local.*          — embedded-issuer events (password change, lockout, reset)
    widget.*         — widget-token events
    keystore.*       — keypair rotation
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request

log = logging.getLogger(__name__)


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None or request.client is None:
        return None
    return request.client.host


def _client_ua(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    return request.headers.get("user-agent")


def audit(
    action: str,
    *,
    admin_id: Optional[str] = None,
    bot_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    request: Optional[Request] = None,
    ip: Optional[str] = None,
    ua: Optional[str] = None,
    success: bool = True,
) -> None:
    """Record a single audit event. Never raises — logs and returns on error."""
    try:
        from models.admin_audit_log import AdminAuditLogs
        AdminAuditLogs.insert(
            admin_id=admin_id,
            bot_id=bot_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
            ip=ip if ip is not None else _client_ip(request),
            ua=ua if ua is not None else _client_ua(request),
            success=success,
        )
    except Exception:
        # Never block the live request on an audit write failure.
        log.exception("audit() failed for action=%s admin=%s bot=%s", action, admin_id, bot_id)
