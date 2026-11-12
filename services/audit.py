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
from contextvars import ContextVar
from typing import Any, Optional

from fastapi import Request

log = logging.getLogger(__name__)

# (ip, user agent) of the HTTP request being handled, set by
# AuditRequestContextMiddleware. audit() falls back to it, so every event
# written while serving a request records who it came from, even when the
# call site (or a service several layers down) has no Request to pass.
_request_meta: ContextVar[Optional[tuple[Optional[str], Optional[str]]]] = ContextVar(
    "audit_request_meta", default=None
)


class AuditRequestContextMiddleware:
    """Pure-ASGI middleware that records the caller's IP and user agent for
    the duration of each HTTP request. Runs inside uvicorn's proxy-headers
    handling, so the IP is the trusted client address (see B5)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        ip = client[0] if client else None
        ua = None
        for name, value in scope.get("headers", []):
            if name == b"user-agent":
                ua = value.decode("latin-1")[:512]
                break
        token = _request_meta.set((ip, ua))
        try:
            await self.app(scope, receive, send)
        finally:
            _request_meta.reset(token)


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
        ctx_ip, ctx_ua = _request_meta.get() or (None, None)
        if ip is None:
            ip = _client_ip(request) if request is not None else ctx_ip
        if ua is None:
            ua = _client_ua(request) if request is not None else ctx_ua
        AdminAuditLogs.insert(
            admin_id=admin_id,
            bot_id=bot_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
            ip=ip,
            ua=ua,
            success=success,
        )
    except Exception:
        # Never block the live request on an audit write failure.
        log.exception("audit() failed for action=%s admin=%s bot=%s", action, admin_id, bot_id)
