"""Transient store for the OIDC dance.

Holds ``OAuthState`` records (state, code_verifier, nonce, return_to) between
``/auth/login`` and ``/auth/callback``. Single-replica only;
Phase 11 swaps in a Redis-backed implementation behind the same interface.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol

from services.auth.base import OAuthState

DEFAULT_TTL = timedelta(minutes=15)


class StateStore(Protocol):
    def put(self, key: str, value: OAuthState, ttl: timedelta = DEFAULT_TTL) -> None: ...
    def pop(self, key: str) -> OAuthState | None: ...


class InMemoryStateStore:
    """Thread-safe dict with lazy expiry on ``pop``."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[OAuthState, datetime]] = {}
        self._lock = threading.Lock()

    def put(self, key: str, value: OAuthState, ttl: timedelta = DEFAULT_TTL) -> None:
        expires_at = datetime.now(timezone.utc) + ttl
        with self._lock:
            self._items[key] = (value, expires_at)

    def pop(self, key: str) -> OAuthState | None:
        now = datetime.now(timezone.utc)
        with self._lock:
            entry = self._items.pop(key, None)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at < now:
            return None
        return value

    def sweep(self) -> int:
        """Drop expired entries. Returns the number removed."""
        now = datetime.now(timezone.utc)
        with self._lock:
            expired = [k for k, (_, exp) in self._items.items() if exp < now]
            for k in expired:
                del self._items[k]
            return len(expired)
