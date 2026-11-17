"""G3a: an admin session ends a fixed time after sign-in.

Before this, the admin UI refreshed its tokens in the background for as
long as a tab was open, and the built-in issuer issued a new 30-day
refresh token on every refresh, so a session never ended by itself.

``AUTH_SESSION_MAX_AGE_SECONDS`` caps a session, counted from sign-in:

- the BFF refresh (``/api/auth/refresh``) refuses to refresh a session
  older than this, and session cookies are never set to outlive it;
- the built-in issuer refuses a refresh token whose sign-in is older
  than this, even when it is presented directly to its token endpoint.

The sign-in time travels in the encrypted refresh cookie and in the
built-in issuer's refresh-token record, and is carried across every
refresh. The last ID token stays valid until its own expiry (the
IdP's token lifetime, usually an hour), so a session ends at most that
long after the limit.

Defaults to 12 hours when ``MODE`` is production-like; ``0`` (or unset
outside production) means no limit.

Idle sessions already end on their own: the UI refreshes only while a
tab is visible, and the session cookies expire with the ID token, so a
session with no visible admin tab for the token's lifetime is over.
"""
from __future__ import annotations

import os
import time
from typing import Optional

MAX_AGE_ENV = "AUTH_SESSION_MAX_AGE_SECONDS"
PROD_DEFAULT_MAX_AGE = 12 * 3600


def max_age_seconds() -> Optional[int]:
    """The configured maximum, or None for no limit."""
    raw = (os.environ.get(MAX_AGE_ENV) or "").strip()
    if not raw:
        from utils.runtime import is_prod_mode

        return PROD_DEFAULT_MAX_AGE if is_prod_mode() else None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{MAX_AGE_ENV} must be a whole number of seconds (got {raw!r})") from None
    if value < 0:
        raise ValueError(f"{MAX_AGE_ENV} can't be negative")
    return value or None


def check_settings() -> None:
    """Boot: a malformed value stops start-up."""
    max_age_seconds()


def is_over(auth_time: Optional[float], now: Optional[float] = None) -> bool:
    """Whether a session that signed in at ``auth_time`` (epoch seconds)
    has reached the maximum age. No sign-in time counts as over only
    when a limit is set and the caller can't tell how old it is."""
    limit = max_age_seconds()
    if limit is None:
        return False
    if auth_time is None:
        return True
    return (now if now is not None else time.time()) - auth_time >= limit


def remaining_seconds(auth_time: Optional[float], now: Optional[float] = None) -> Optional[int]:
    """Seconds left in the session, or None when there is no limit."""
    limit = max_age_seconds()
    if limit is None or auth_time is None:
        return None
    return int(auth_time + limit - (now if now is not None else time.time()))
