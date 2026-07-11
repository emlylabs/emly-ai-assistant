"""``pending_admin`` table — pre-staged invitees.

When a superadmin invites someone to the deployment, we don't issue a token
ourselves (the IdP owns identity). Instead we record ``(email, is_superadmin,
bot_assignments)``. On first matching IdP login the row is *consumed*: the
admin is created and the bot memberships are applied.

This pattern works for any provider — embedded issuer, Auth0, Clerk, Cognito,
Keycloak — without us minting any tokens or sending any emails.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    DoesNotExist,
    Model,
    TextField,
)
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel, EmailStr

from db.db import DB

log = logging.getLogger(__name__)


class PendingAdmin(Model):
    id = CharField(max_length=64, primary_key=True)
    email = CharField(max_length=255, unique=True)
    invited_by = CharField(max_length=64, null=True)
    is_superadmin = BooleanField(default=False)
    bot_assignments = TextField(null=True)  # JSON: [{"bot_id": "...", "role": "owner|admin|viewer"}, ...]
    created_at = DateTimeField()
    expires_at = DateTimeField(null=True)
    consumed_at = DateTimeField(null=True)
    consumed_by = CharField(max_length=64, null=True)

    class Meta:
        database = DB
        table_name = "pending_admin"


class PendingAdminModel(BaseModel):
    id: str
    email: EmailStr
    invited_by: Optional[str] = None
    is_superadmin: bool = False
    bot_assignments: List[dict] = []
    created_at: datetime
    expires_at: Optional[datetime] = None
    consumed_at: Optional[datetime] = None
    consumed_by: Optional[str] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_assignments(raw: Optional[str]) -> List[dict]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _to_pydantic(row: PendingAdmin) -> PendingAdminModel:
    data = model_to_dict(row, recurse=False)
    return PendingAdminModel(
        id=data["id"],
        email=data["email"],
        invited_by=data["invited_by"],
        is_superadmin=data["is_superadmin"],
        bot_assignments=_parse_assignments(data["bot_assignments"]),
        created_at=data["created_at"],
        expires_at=data["expires_at"],
        consumed_at=data["consumed_at"],
        consumed_by=data["consumed_by"],
    )


class PendingAdminsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([PendingAdmin])

    def create(
        self,
        *,
        email: str,
        invited_by: Optional[str] = None,
        is_superadmin: bool = False,
        bot_assignments: Optional[List[dict]] = None,
        expires_at: Optional[datetime] = None,
    ) -> PendingAdminModel:
        row_id = f"pend-{uuid.uuid4()}"
        PendingAdmin.create(
            id=row_id,
            email=email.lower(),
            invited_by=invited_by,
            is_superadmin=is_superadmin,
            bot_assignments=json.dumps(bot_assignments or []),
            created_at=_now(),
            expires_at=expires_at,
        )
        return _to_pydantic(PendingAdmin.get(PendingAdmin.id == row_id))

    def get_active_by_email(self, email: str) -> Optional[PendingAdminModel]:
        """Return the unconsumed, unexpired pending row for the email (if any)."""
        try:
            row = PendingAdmin.get(PendingAdmin.email == email.lower())
        except DoesNotExist:
            return None
        if row.consumed_at is not None:
            return None
        if row.expires_at is not None and row.expires_at < _now():
            return None
        return _to_pydantic(row)

    def list_pending(self) -> List[PendingAdminModel]:
        rows = (
            PendingAdmin
            .select()
            .where(PendingAdmin.consumed_at.is_null())
            .order_by(PendingAdmin.created_at.desc())
        )
        return [_to_pydantic(r) for r in rows]

    def consume(self, email: str, *, by_admin_id: str) -> bool:
        rows = (
            PendingAdmin.update(consumed_at=_now(), consumed_by=by_admin_id)
            .where(
                (PendingAdmin.email == email.lower())
                & (PendingAdmin.consumed_at.is_null())
            )
            .execute()
        )
        return rows > 0

    def revoke(self, email: str) -> bool:
        rows = PendingAdmin.delete().where(PendingAdmin.email == email.lower()).execute()
        return rows > 0


PendingAdmins = PendingAdminsTable(DB)
