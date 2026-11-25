"""One Zoho CRM or Salesforce connection per bot.

The client secret, access token and refresh token are Fernet-sealed.
Everything else is plain so the admin page can show status and expiry
without decrypting a secret. The client secret is never returned by the
admin API; ``to_public`` is the only shape that leaves this module.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from peewee import CharField, DateTimeField, IntegerField, Model, TextField

from crm.providers import PROVIDERS, salesforce_login_server, scope_string, zoho_accounts_server
from db.db import DB
from services.secrets import encrypt


class CrmConnection(Model):
    id = CharField(max_length=64, primary_key=True)
    bot_id = CharField(max_length=255)
    provider = CharField(max_length=32)
    status = CharField(max_length=32, default="pending")
    client_id = CharField(max_length=512)
    client_secret_encrypted = TextField(null=True)
    data_center = CharField(max_length=16, null=True)
    accounts_server = CharField(max_length=255, null=True)
    api_domain = CharField(max_length=255, null=True)
    hub_id = CharField(max_length=64, null=True)  # HubSpot only; HubSpot was removed
    scopes = TextField(null=True)
    access_token_encrypted = TextField(null=True)
    refresh_token_encrypted = TextField(null=True)
    token_issued_at = DateTimeField(null=True)
    expires_in = IntegerField(null=True)
    expires_at = DateTimeField(null=True)
    last_error = TextField(null=True)
    created_at = DateTimeField()
    updated_at = DateTimeField()

    class Meta:
        database = DB
        table_name = "crm_connection"
        indexes = ((("bot_id", "provider"), True),)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _credential(value: str, field: str, limit: int) -> str:
    text = (value or "").strip()
    if not text or any(ch.isspace() for ch in text) or len(text) > limit:
        raise ValueError(f"{field} is not valid")
    return text


def to_public(row: CrmConnection) -> dict:
    """Admin-facing connection. No secret, access token, or refresh token."""
    return {
        "provider": row.provider,
        "status": row.status,
        "client_id": row.client_id,
        "has_secret": bool(row.client_secret_encrypted),
        "data_center": row.data_center,
        "expires_at": _iso(row.expires_at),
        "token_issued_at": _iso(row.token_issued_at),
        "expires_in": row.expires_in,
        # Salesforce: the login origin, so the page can show a saved My Domain URL.
        "login_url": row.accounts_server if row.provider == "salesforce" else None,
        "scopes": row.scopes,
        "last_error": row.last_error,
        "api_domain": row.api_domain,
    }


class CrmConnectionsTable:
    def __init__(self, db):
        self.db = db
        db.create_tables([CrmConnection])

    def get(self, bot_id: str, provider: str) -> Optional[CrmConnection]:
        return CrmConnection.get_or_none(
            (CrmConnection.bot_id == bot_id) & (CrmConnection.provider == provider)
        )

    def list_for_bot(self, bot_id: str) -> list:
        # Rows for a CRM this app no longer supports (HubSpot) stay in the
        # table but are not listed or synced.
        return list(
            CrmConnection.select()
            .where((CrmConnection.bot_id == bot_id) & CrmConnection.provider.in_(list(PROVIDERS)))
            .order_by(CrmConnection.provider)
        )

    def _syncable(self, bot_id: str, providers: Optional[tuple] = None):
        """Connections a lead can go to. ``providers`` narrows them; ``None`` is all."""
        where = (
            (CrmConnection.bot_id == bot_id)
            & CrmConnection.provider.in_(list(PROVIDERS))
            & (CrmConnection.status.in_(("connected", "error")))
            & (CrmConnection.refresh_token_encrypted.is_null(False))
            & (CrmConnection.refresh_token_encrypted != "")
        )
        if providers is not None:
            where &= CrmConnection.provider.in_(list(providers))
        return CrmConnection.select().where(where)

    def list_syncable(self, bot_id: str, providers: Optional[tuple] = None) -> list:
        if providers is not None and not providers:
            return []
        return list(self._syncable(bot_id, providers))

    def has_syncable(self, bot_id: str, providers: Optional[tuple] = None) -> bool:
        if providers is not None and not providers:
            return False
        return self._syncable(bot_id, providers).exists()

    def upsert_credentials(
        self,
        bot_id: str,
        provider: str,
        client_id: str,
        client_secret: Optional[str],
        data_center: Optional[str],
        login_url: Optional[str] = None,
    ) -> str:
        """Save the app credentials and drop any tokens. The admin connects again after this.

        ``login_url`` is only read for a Salesforce Developer Edition org: its My Domain URL.
        """
        if provider not in PROVIDERS:
            raise ValueError("unknown CRM")
        client_id = _credential(client_id, "client id", 512)
        row = self.get(bot_id, provider)
        if client_secret and client_secret.strip():
            encrypted = encrypt(_credential(client_secret, "client secret", 2048))
        elif row is not None and row.client_secret_encrypted:
            encrypted = row.client_secret_encrypted
        else:
            raise ValueError("client secret is required")
        center = None
        accounts = None
        if provider == "zoho":
            center = (data_center or "").strip().lower()
            accounts = zoho_accounts_server(center)
        elif provider == "salesforce":
            # ``data_center`` holds the environment; the login host follows from
            # it, or for Developer Edition is the org's My Domain URL.
            center = (data_center or "production").strip().lower()
            accounts = salesforce_login_server(center, login_url)
        now = datetime.utcnow()
        fields = {
            "client_id": client_id,
            "client_secret_encrypted": encrypted,
            "data_center": center,
            "accounts_server": accounts,
            "status": "pending",
            "scopes": scope_string(provider),
            "access_token_encrypted": None,
            "refresh_token_encrypted": None,
            "token_issued_at": None,
            "expires_in": None,
            "expires_at": None,
            "api_domain": None,
            "hub_id": None,
            "last_error": None,
            "updated_at": now,
        }
        if row is not None:
            CrmConnection.update(**fields).where(CrmConnection.id == row.id).execute()
            return row.id
        new_id = str(uuid.uuid4())
        CrmConnection.create(
            id=new_id, bot_id=bot_id, provider=provider, created_at=now, **fields
        )
        return new_id

    def save_tokens(
        self,
        connection_id: str,
        *,
        access_token: str,
        refresh_token: str,
        issued_at: datetime,
        expires_in: int,
        expires_at: datetime,
        api_domain: Optional[str] = None,
        scopes: Optional[str] = None,
        accounts_server: Optional[str] = None,
        data_center: Optional[str] = None,
    ) -> None:
        if not access_token or not refresh_token:
            raise ValueError("access token and refresh token are required")
        fields = {
            "access_token_encrypted": encrypt(access_token),
            "refresh_token_encrypted": encrypt(refresh_token),
            "token_issued_at": issued_at,
            "expires_in": int(expires_in),
            "expires_at": expires_at,
            "status": "connected",
            "last_error": None,
            "updated_at": datetime.utcnow(),
        }
        if api_domain:
            fields["api_domain"] = api_domain
        if scopes:
            fields["scopes"] = scopes
        if accounts_server:
            fields["accounts_server"] = accounts_server
        if data_center:
            fields["data_center"] = data_center
        CrmConnection.update(**fields).where(CrmConnection.id == connection_id).execute()

    def record_error(self, connection_id: str, message: str) -> None:
        """Mark the connection failed without dropping the refresh token.

        A later capture tries again. A successful sync clears the error.
        """
        CrmConnection.update(
            status="error",
            last_error=(message or "CRM request failed")[:300],
            updated_at=datetime.utcnow(),
        ).where(CrmConnection.id == connection_id).execute()

    def mark_synced(self, connection_id: str) -> None:
        CrmConnection.update(
            status="connected",
            last_error=None,
            updated_at=datetime.utcnow(),
        ).where(CrmConnection.id == connection_id).execute()

    def disconnect(self, bot_id: str, provider: str) -> bool:
        deleted = (
            CrmConnection.delete()
            .where((CrmConnection.bot_id == bot_id) & (CrmConnection.provider == provider))
            .execute()
        )
        return deleted > 0


CrmConnections = CrmConnectionsTable(DB)
