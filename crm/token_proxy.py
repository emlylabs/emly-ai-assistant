"""A proxy that hands adapters a live access token and hides expiry.

Adapters ask for ``access_token()``. When ``now >= expires_at - 60s`` this
refreshes, stores the new access token and expiry, and stores a rotated
refresh token when the provider returned one. Zoho usually does not, and
the previous refresh token is kept.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.fernet import InvalidToken

from crm.contracts import CrmAuthError
from services.secrets import decrypt

REFRESH_LEEWAY_SECONDS = 60

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _connection_lock(connection_id: str) -> threading.Lock:
    """One refresh at a time per connection, across lead-sync threads.

    HubSpot invalidates the previous refresh token when it issues a new one.
    Two overlapping refreshes would save the loser and then fail forever.
    """
    with _LOCKS_GUARD:
        lock = _LOCKS.get(connection_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[connection_id] = lock
        return lock


@dataclass
class TokenRecord:
    access_token: str
    refresh_token: Optional[str]
    issued_at: Optional[datetime]
    expires_in: int
    expires_at: Optional[datetime]
    api_domain: Optional[str] = None
    hub_id: Optional[str] = None
    scopes: Optional[str] = None


class TokenSource(ABC):
    """Load and store one connection's tokens. The proxy does not know the table."""

    @abstractmethod
    async def load(self) -> TokenRecord:
        """The tokens currently stored."""

    @abstractmethod
    async def refresh(self, refresh_token: str) -> TokenRecord:
        """Exchange ``refresh_token``. ``refresh_token`` on the result is None when the provider omitted one."""

    @abstractmethod
    async def save(self, record: TokenRecord) -> None:
        """Persist a record the proxy has already filled in."""


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _expiry(record: TokenRecord) -> Optional[datetime]:
    """Prefer ``expires_at``. Fall back to ``issued_at + expires_in``."""
    if record.expires_at is not None:
        return _naive_utc(record.expires_at)
    if record.issued_at is not None and record.expires_in:
        return _naive_utc(record.issued_at) + timedelta(seconds=int(record.expires_in))
    return None


def token_is_due(record: TokenRecord, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    if not record.access_token:
        return True
    expiry = _expiry(record)
    if expiry is None:
        return True
    return now >= expiry - timedelta(seconds=REFRESH_LEEWAY_SECONDS)


class RefreshingTokenProxy:
    """``TokenSource`` plus the expiry rule. Adapters only see ``access_token``."""

    def __init__(self, connection_id: str, source: TokenSource):
        self._connection_id = connection_id
        self._source = source

    async def access_token(self) -> str:
        # Held across the refresh HTTP call on purpose: each lead sync runs
        # in its own thread, and this lock stops a second thread refreshing
        # the same HubSpot connection at the same time.
        with _connection_lock(self._connection_id):
            record = await self._source.load()
            if not token_is_due(record):
                return record.access_token
            return await self._refresh(record)

    async def refresh_rejected(self, rejected: str) -> str:
        """The provider refused ``rejected`` before its expiry. Refresh once.

        Salesforce sends no expiry, and an admin can end sessions early. If
        another thread already replaced the token, that one is returned.
        """
        with _connection_lock(self._connection_id):
            record = await self._source.load()
            if record.access_token and record.access_token != rejected:
                return record.access_token
            return await self._refresh(record)

    async def _refresh(self, record: TokenRecord) -> str:
        if not record.refresh_token:
            raise CrmAuthError("no refresh token")
        refreshed = await self._source.refresh(record.refresh_token)
        if not refreshed.access_token:
            raise CrmAuthError("refresh returned no access token")
        if not refreshed.refresh_token:
            refreshed = replace(refreshed, refresh_token=record.refresh_token)
        await self._source.save(refreshed)
        return refreshed.access_token


class ConnectionTokenSource(TokenSource):
    """The production source: one ``crm_connection`` row and that vendor's OAuth client."""

    def __init__(self, connection_id: str, oauth):
        self._connection_id = connection_id
        self._oauth = oauth

    def _row(self):
        from models.crm_connections import CrmConnection

        row = CrmConnection.get_or_none(CrmConnection.id == self._connection_id)
        if row is None:
            raise CrmAuthError("CRM connection is gone")
        return row

    async def load(self) -> TokenRecord:
        row = self._row()
        try:
            access = decrypt(row.access_token_encrypted) or ""
            refresh = decrypt(row.refresh_token_encrypted) or ""
        except InvalidToken:
            raise CrmAuthError("stored CRM token cannot be read") from None
        return TokenRecord(
            access_token=access,
            refresh_token=refresh or None,
            issued_at=row.token_issued_at,
            expires_in=row.expires_in or 0,
            expires_at=row.expires_at,
            api_domain=row.api_domain,
            hub_id=row.hub_id,
            scopes=row.scopes,
        )

    async def refresh(self, refresh_token: str) -> TokenRecord:
        from models.crm_connections import CrmConnections

        row = self._row()
        try:
            secret = decrypt(row.client_secret_encrypted)
        except InvalidToken:
            secret = None
        if not secret or not row.client_id:
            raise CrmAuthError("CRM credentials are incomplete")
        try:
            grant = await self._oauth.refresh(
                client_id=row.client_id,
                client_secret=secret,
                refresh_token=refresh_token,
                accounts_server=row.accounts_server,
            )
        except CrmAuthError as exc:
            CrmConnections.record_error(row.id, str(exc))
            raise
        api_domain = None
        if grant.api_domain:
            try:
                api_domain = self._oauth.allow_api_domain(grant.api_domain)
            except ValueError:
                CrmConnections.record_error(row.id, "token response named an unexpected API host")
                raise CrmAuthError("token response named an unexpected API host") from None
        now = datetime.utcnow()
        return TokenRecord(
            access_token=grant.access_token,
            refresh_token=grant.refresh_token,
            issued_at=now,
            expires_in=grant.expires_in,
            expires_at=now + timedelta(seconds=grant.expires_in),
            api_domain=api_domain,
            hub_id=grant.hub_id,
            scopes=grant.scopes,
        )

    async def save(self, record: TokenRecord) -> None:
        from models.crm_connections import CrmConnections

        CrmConnections.save_tokens(
            self._connection_id,
            access_token=record.access_token,
            refresh_token=record.refresh_token or "",
            issued_at=record.issued_at or datetime.utcnow(),
            expires_in=record.expires_in,
            expires_at=record.expires_at or datetime.utcnow(),
            api_domain=record.api_domain,
            hub_id=record.hub_id,
            scopes=record.scopes,
        )
