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
    data.*           — reads of conversations and visitor details (opt-in, G5e)
"""

from __future__ import annotations

import logging
import threading
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


# The ASGI scope of that request, so audit rows can name the route. Read
# lazily: routing fills in ``scope["route"]`` after this middleware runs.
_request_scope: ContextVar[Optional[dict]] = ContextVar("audit_request_scope", default=None)


def current_request_route() -> tuple[Optional[str], Optional[str]]:
    """``(method, route)`` of the request being handled, e.g.
    ``("GET", "/api/admin/bots/{slug}/sessions")``. The route is the
    matched path template when routing has run, else the raw path.
    ``(None, None)`` outside a request."""
    scope = _request_scope.get()
    if not scope:
        return None, None
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template:
        template = (scope.get("root_path") or "") + template
    return scope.get("method"), template or scope.get("path")


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
        scope_token = _request_scope.set(scope)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_scope.reset(scope_token)
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
    """Record a single audit event. Never raises — logs and returns on error
    (except ``AuditUnavailable`` when fail-closed applies)."""
    row_id = None
    try:
        from models.admin_audit_log import AdminAuditLogs
        ctx_ip, ctx_ua = _request_meta.get() or (None, None)
        if ip is None:
            ip = _client_ip(request) if request is not None else ctx_ip
        if ua is None:
            ua = _client_ua(request) if request is not None else ctx_ua
        row_id = AdminAuditLogs.insert(
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
    except Exception as exc:
        # Never block the live request on an audit write failure, but make
        # it loud: ERROR log (event name and error type only; the exception
        # text can echo the payload through SQL parameters), a counter, and
        # a warning on /api/readyz (G5c).
        global _WRITE_FAILURES
        with _FAILURE_LOCK:
            _WRITE_FAILURES += 1
        log.error("AUDIT WRITE FAILED: action=%s admin=%s bot=%s error=%s",
                  action, admin_id, bot_id, type(exc).__name__)
        _emit_stdout(None, action, admin_id, bot_id, target_type, target_id, payload, ip, ua, success)
        if fails_closed(action):
            raise AuditUnavailable(action) from None
        return
    _emit_stdout(row_id, action, admin_id, bot_id, target_type, target_id, payload, ip, ua, success)


# -------- SIEM copy on stdout (Q-G5b) --------

# The audit table is an ordinary table: anyone with database access can
# change or delete rows. AUDIT_LOG_STDOUT=true also writes every event as
# one JSON line on standard output, where the container platform's log
# collector ships it to the customer's SIEM, out of the app's reach.
STDOUT_ENV = "AUDIT_LOG_STDOUT"
_STDOUT_LOCK = threading.Lock()


def stdout_enabled() -> bool:
    from utils.runtime import env_flag

    return env_flag(STDOUT_ENV, default=False)


def _emit_stdout(row_id, action, admin_id, bot_id, target_type, target_id, payload, ip, ua,
                 success) -> None:
    """One JSON line per event, written straight to stdout (not through
    logging, whose filters would rewrite it). ``stored`` says whether the
    database row was written. Never raises."""
    if not stdout_enabled():
        return
    try:
        import json
        import sys
        from datetime import datetime, timezone

        line = json.dumps({
            "type": "emly.audit",
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "id": row_id,
            "stored": row_id is not None,
            "action": action,
            "success": success,
            "admin_id": admin_id,
            "bot_id": bot_id,
            "target_type": target_type,
            "target_id": target_id,
            "ip": ip,
            "ua": ua[:512] if ua else None,
            "payload": payload,
        }, default=str, separators=(",", ":"))
        with _STDOUT_LOCK:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    except Exception:
        log.error("AUDIT STDOUT WRITE FAILED: action=%s", action)


# -------- fail closed (Q-G5a) --------

# Sign-ins, permission and configuration changes: the events the product
# promises are audited. Content events (messages, files, sessions, crawls)
# only log loudly when their audit write fails.
SECURITY_ACTION_PREFIXES = (
    "auth.", "authz.", "csrf.", "admin.", "membership.", "pending.", "invite.", "local.",
    "keystore.", "bot.", "bots.", "config.", "channel.", "workspace.", "audit.",
    # Written only when AUDIT_DATA_ACCESS is on (G5e): an operator who
    # asked for reads to be audited doesn't want unaudited reads.
    "data.",
)
FAIL_CLOSED_ENV = "AUDIT_FAIL_CLOSED"


class AuditUnavailable(RuntimeError):
    """A security event's audit row couldn't be written while serving a
    request with fail-closed on. main.py turns it into a 503."""


def fails_closed(action: str) -> bool:
    """Whether a failed write of ``action`` must fail the request: a
    security event, during an HTTP request (background jobs only log), with
    ``AUDIT_FAIL_CLOSED`` on (the default when ``MODE`` is production-like)."""
    from utils.runtime import env_flag, is_prod_mode

    return (
        action.startswith(SECURITY_ACTION_PREFIXES)
        and _request_scope.get() is not None
        and env_flag(FAIL_CLOSED_ENV, default=is_prod_mode())
    )


_WRITE_FAILURES = 0
_FAILURE_LOCK = threading.Lock()


def audit_write_failures() -> int:
    """Audit writes that failed since this process started."""
    return _WRITE_FAILURES


# -------- retention (C7) --------

def audit_retention_days() -> int:
    """AUDIT_RETENTION_DAYS (default 365). 0 or less keeps rows forever."""
    import os

    try:
        return int(os.environ.get("AUDIT_RETENTION_DAYS", "365"))
    except ValueError:
        log.warning("AUDIT_RETENTION_DAYS is not an integer; using 365")
        return 365


def purge_expired_audit_rows() -> int:
    """Daily job: delete audit rows older than the retention window and
    record the purge itself. Module-level so APScheduler's persistent
    jobstore can reference it by name."""
    from datetime import datetime, timedelta, timezone

    days = audit_retention_days()
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        from models.admin_audit_log import AdminAuditLogs

        removed = AdminAuditLogs.purge_older_than(cutoff)
    except Exception:
        log.exception("audit retention purge failed")
        return 0
    if removed:
        log.info("audit retention: removed %d row(s) older than %d days", removed, days)
        audit("audit.purged", payload={"removed": removed, "retention_days": days,
                                       "cutoff": cutoff.isoformat()})
    return removed
