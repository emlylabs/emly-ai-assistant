"""Rate-limit wiring for auth-sensitive endpoints.

Uses ``slowapi`` (single-replica, in-memory). Phase 11 of the plan swaps in
``fastapi-limiter`` once Redis is available so limits are shared across
workers/replicas.

Limits (env-overridable):
- ``/api/admin/auth/login``       — 10/min per IP
- ``/api/admin/auth/callback``    — 20/min per IP (legitimate retries during testing)
- ``/api/auth/local/authorize``   — 5/min per IP
- ``/api/auth/local/token``       — 10/min per IP
- ``/widget/{ref}/init``          — 30/min per IP

These are intentionally conservative — they're meant to slow brute-force,
not to gate normal operator usage. Tune via env if your environment needs it.
"""

from __future__ import annotations

import logging
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

log = logging.getLogger(__name__)

_LIMITS = {
    "auth_login": os.environ.get("AUTH_RATE_LOGIN", "10/minute"),
    "auth_callback": os.environ.get("AUTH_RATE_CALLBACK", "20/minute"),
    "local_authorize": os.environ.get("AUTH_RATE_LOCAL_AUTHORIZE", "5/minute"),
    "local_token": os.environ.get("AUTH_RATE_LOCAL_TOKEN", "10/minute"),
    "widget_init": os.environ.get("AUTH_RATE_WIDGET_INIT", "30/minute"),
}


limiter = Limiter(key_func=get_remote_address, default_limits=[])


def limit_for(name: str) -> str:
    """Resolve a named limit string. Unknown names fall back to a permissive default."""
    return _LIMITS.get(name, "60/minute")
