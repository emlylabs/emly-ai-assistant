"""Rate-limit wiring for auth-sensitive endpoints.

Uses ``slowapi`` (single-replica, in-memory). Phase 11 of the plan swaps in
``fastapi-limiter`` once Redis is available so limits are shared across
workers/replicas.

Limits (env-overridable):
- ``/api/admin/auth/login``       — 10/min per IP
- ``/api/admin/auth/callback``    — 20/min per IP (legitimate retries during testing)
- ``/api/auth/local/authorize``   — 5/min per IP (GET form + POST submit share it)
- ``/api/auth/local/token``       — 10/min per IP
- ``/widget/{ref}/init``          — 30/min per IP per bot
- ``/widget/{ref}/chat``          — 20/min per IP per bot
- ``/widget/{ref}/action``        — 20/min per IP per bot
- widget uploads (action w/ files) — 10/min per IP per bot, on top of the above
- OTP send                        — 5 per 10 min per (bot, email) and 20/hour per IP
- OTP verify                      — 10 per 10 min per IP per bot

Decorator limits (``@limiter.limit``) need a ``request: Request`` parameter.
Routes that can't satisfy that (the chat route names its body ``request``)
or that only know the key after parsing the body (OTP) use ``rate_limit()``
/ ``hit()`` below, which share the same in-memory storage.

These are intentionally conservative — they're meant to slow brute-force,
not to gate normal operator usage. Tune via env if your environment needs it.
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import HTTPException, Request, status
from limits import parse as parse_limit
from slowapi import Limiter
from slowapi.util import get_remote_address

log = logging.getLogger(__name__)

_LIMITS = {
    "auth_login": os.environ.get("AUTH_RATE_LOGIN", "10/minute"),
    "auth_callback": os.environ.get("AUTH_RATE_CALLBACK", "20/minute"),
    "local_authorize": os.environ.get("AUTH_RATE_LOCAL_AUTHORIZE", "5/minute"),
    "local_token": os.environ.get("AUTH_RATE_LOCAL_TOKEN", "10/minute"),
    "widget_init": os.environ.get("AUTH_RATE_WIDGET_INIT", "30/minute"),
    "widget_chat": os.environ.get("AUTH_RATE_WIDGET_CHAT", "20/minute"),
    "widget_action": os.environ.get("AUTH_RATE_WIDGET_ACTION", "20/minute"),
    "widget_upload": os.environ.get("AUTH_RATE_WIDGET_UPLOAD", "10/minute"),
    "otp_send_target": os.environ.get("AUTH_RATE_OTP_SEND_TARGET", "5/10minutes"),
    "otp_send_ip": os.environ.get("AUTH_RATE_OTP_SEND_IP", "20/hour"),
    "otp_verify": os.environ.get("AUTH_RATE_OTP_VERIFY", "10/10minutes"),
}


limiter = Limiter(key_func=get_remote_address, default_limits=[])


def limit_for(name: str) -> str:
    """Resolve a named limit string. Unknown names fall back to a permissive default."""
    return _LIMITS.get(name, "60/minute")


def client_ip(request: Request) -> str:
    """Rate-limit key for the caller. Honours X-Forwarded-For only as far as
    uvicorn's ``--forwarded-allow-ips`` trusts the proxy (see B5)."""
    return get_remote_address(request) or "unknown"


def hit(name: str, *key_parts: str) -> None:
    """Count one request against limit ``name`` for ``key_parts``; raise
    429 with ``Retry-After`` once the limit is exceeded."""
    if not limiter.enabled:
        return
    item = parse_limit(limit_for(name))
    keys = (name, *[str(k) for k in key_parts])
    if limiter.limiter.hit(item, *keys):
        return
    try:
        reset_at, _remaining = limiter.limiter.get_window_stats(item, *keys)
        retry_after = max(1, int(reset_at - time.time()))
    except Exception:
        retry_after = 60
    log.info("rate limit exceeded name=%s", name)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "rate_limited", "limit": str(item)},
        headers={"Retry-After": str(retry_after)},
    )


def rate_limit(name: str, *, per_path_param: str | None = None):
    """FastAPI dependency: limit ``name`` per client IP (and per value of a
    path parameter such as the bot ref, when given). Runs before body
    validation and before the handler."""

    def _dependency(request: Request) -> None:
        scope = request.path_params.get(per_path_param, "") if per_path_param else ""
        hit(name, client_ip(request), scope)

    return _dependency
