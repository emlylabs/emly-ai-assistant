"""In-memory store for refresh tokens issued by the embedded OIDC issuer.

The embedded issuer's ``/api/auth/local/token`` endpoint mints a
refresh_token alongside the id_token when the authorization request
included the ``offline_access`` scope. The opaque refresh_token string
is stored in this map keyed by the token itself, holding the resolved
admin identity so a subsequent ``grant_type=refresh_token`` exchange
can mint a fresh id_token + access_token without re-asking for the
password.

Lifecycle:
- Minted on the initial ``authorization_code`` exchange (if
  ``offline_access`` was in the granted scope).
- Consumed (popped) on the ``refresh_token`` grant — single use, just
  like the authorization code. The IdP rotates the refresh token on
  every successful refresh.
- Optional explicit revocation: a logout call that carries a
  refresh_token drops it from the store so a stolen-token replay fails.

This is the same single-replica pattern as ``InMemoryCodeStore`` in
``flows.py``. The auth-plan.md Phase 11 swap-in target is a
Redis-backed store behind the same Protocol; the routes only ever
interact with the ``RefreshTokenStore`` Protocol.
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

log = logging.getLogger(__name__)

DEFAULT_REFRESH_TTL = timedelta(days=30)


@dataclass(frozen=True)
class RefreshTokenRecord:
    """Per-refresh-token state. Single-use; popped at the refresh grant."""

    token: str
    admin_id: str
    email: str
    email_verified: bool
    name: Optional[str]
    scope: str
    issued_at: datetime


class RefreshTokenStore(Protocol):
    def put(self, record: RefreshTokenRecord, ttl: timedelta = DEFAULT_REFRESH_TTL) -> None: ...
    def pop(self, token: str) -> Optional[RefreshTokenRecord]: ...
    def revoke(self, token: str) -> bool: ...


class InMemoryRefreshTokenStore:
    """Thread-safe dict with lazy expiry on pop. Single-replica only."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[RefreshTokenRecord, datetime]] = {}
        self._lock = threading.Lock()

    def put(self, record: RefreshTokenRecord, ttl: timedelta = DEFAULT_REFRESH_TTL) -> None:
        expires_at = datetime.now(timezone.utc) + ttl
        with self._lock:
            self._items[record.token] = (record, expires_at)

    def pop(self, token: str) -> Optional[RefreshTokenRecord]:
        now = datetime.now(timezone.utc)
        with self._lock:
            entry = self._items.pop(token, None)
        if entry is None:
            return None
        record, expires_at = entry
        if expires_at < now:
            return None
        return record

    def revoke(self, token: str) -> bool:
        """Drop a refresh token (logout). Returns True if the token was present."""
        with self._lock:
            return self._items.pop(token, None) is not None

    def sweep(self) -> int:
        """Drop expired entries. Returns the number removed."""
        now = datetime.now(timezone.utc)
        with self._lock:
            expired = [k for k, (_, exp) in self._items.items() if exp < now]
            for k in expired:
                del self._items[k]
            return len(expired)


def generate_refresh_token() -> str:
    """Opaque, URL-safe token — 32 bytes of entropy, base64url-encoded."""
    return secrets.token_urlsafe(32)
