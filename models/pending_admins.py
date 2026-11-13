"""``pending_admin`` table — pre-staged invitees.

Two flavours of row coexist:

* ``kind='space_invite'`` — new-style. Has a ``token`` (URL-safe random),
  carries explicit ``status`` (``pending``/``accepted``/``rejected``/
  ``expired``/``revoked``), and is acted on via the email link. Bot
  owners and superadmins can create these.
* ``kind='superadmin_legacy'`` — old-style. No token, applied silently
  on the invitee's first matching OIDC login. Preserved verbatim for
  the existing platform-invite + bootstrap flow.

Operators stage new-style invites via ``POST /admins/invites`` (per-bot
owner-gated) or ``POST /admins/pending`` (superadmin, multi-bot +
``is_superadmin``). The resolver in ``services/auth/user_resolver``
consumes only ``superadmin_legacy`` rows silently; new-style rows wait
for an explicit accept/reject from the recipient.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
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


# Status enum constants — kept as module-level strings so callers can
# import them rather than re-type the literal.
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"
ALL_STATUSES = (
    STATUS_PENDING,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    STATUS_EXPIRED,
    STATUS_REVOKED,
)

# Kind enum constants — see module docstring.
KIND_SPACE_INVITE = "space_invite"
KIND_SUPERADMIN_LEGACY = "superadmin_legacy"


class PendingAdmin(Model):
    id = CharField(max_length=64, primary_key=True)
    email = CharField(max_length=255)
    invited_by = CharField(max_length=64, null=True)
    is_superadmin = BooleanField(default=False)
    bot_assignments = TextField(null=True)  # JSON: [{"bot_id": "...", "role": "owner|admin|viewer"}, ...]
    created_at = DateTimeField()
    expires_at = DateTimeField(null=True)
    consumed_at = DateTimeField(null=True)
    consumed_by = CharField(max_length=64, null=True)
    # 008_space_invites_and_default_space
    token = CharField(max_length=64, null=True)
    status = CharField(max_length=16, default=STATUS_PENDING)
    kind = CharField(max_length=24, default=KIND_SUPERADMIN_LEGACY)
    target_bot_id = CharField(max_length=64, null=True)
    responded_at = DateTimeField(null=True)
    responded_by = CharField(max_length=64, null=True)

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
    token: Optional[str] = None
    status: str = STATUS_PENDING
    kind: str = KIND_SUPERADMIN_LEGACY
    target_bot_id: Optional[str] = None
    responded_at: Optional[datetime] = None
    responded_by: Optional[str] = None


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
        token=data.get("token"),
        status=data.get("status") or STATUS_PENDING,
        kind=data.get("kind") or KIND_SUPERADMIN_LEGACY,
        target_bot_id=data.get("target_bot_id"),
        responded_at=data.get("responded_at"),
        responded_by=data.get("responded_by"),
    )


def _expired(row: PendingAdmin) -> bool:
    expires_at = row.expires_at
    if expires_at is None:
        return False
    now = datetime.utcnow()
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        return expires_at < now
    return False


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
        kind: str = KIND_SUPERADMIN_LEGACY,
        token: Optional[str] = None,
        target_bot_id: Optional[str] = None,
    ) -> PendingAdminModel:
        row_id = f"pend-{uuid.uuid4()}"
        # Default TTL — keeps stale invites from accumulating forever.
        # Operators can override via ``AUTH_PENDING_INVITE_TTL_DAYS``;
        # set to 0 (or pass an explicit ``expires_at``) to opt out.
        # Stored as naive UTC to match Peewee's SQLite serialization;
        # the comparison in ``get_active_by_email`` is normalised to
        # naive UTC too.
        if expires_at is None:
            try:
                ttl_days = int(os.environ.get("AUTH_PENDING_INVITE_TTL_DAYS", "30"))
            except (TypeError, ValueError):
                ttl_days = 30
            if ttl_days > 0:
                expires_at = datetime.utcnow() + timedelta(days=ttl_days)
        PendingAdmin.create(
            id=row_id,
            email=email.lower(),
            invited_by=invited_by,
            is_superadmin=is_superadmin,
            bot_assignments=json.dumps(bot_assignments or []),
            created_at=_now(),
            expires_at=expires_at,
            token=token,
            status=STATUS_PENDING,
            kind=kind,
            target_bot_id=target_bot_id,
        )
        return _to_pydantic(PendingAdmin.get(PendingAdmin.id == row_id))

    def create_space_invite(
        self,
        *,
        email: str,
        invited_by: Optional[str],
        target_bot_id: str,
        role: str,
        is_superadmin: bool = False,
        expires_at: Optional[datetime] = None,
    ) -> PendingAdminModel:
        """Convenience for the bot-owner-scoped invite path. Mints a token,
        wraps the (bot, role) pair into the standard ``bot_assignments``
        list, and tags the row as ``space_invite`` so the resolver leaves
        it for explicit accept/reject."""
        return self.create(
            email=email,
            invited_by=invited_by,
            is_superadmin=is_superadmin,
            bot_assignments=[{"bot_id": target_bot_id, "role": role}],
            expires_at=expires_at,
            kind=KIND_SPACE_INVITE,
            token=secrets.token_urlsafe(32),
            target_bot_id=target_bot_id,
        )

    def get_by_id(self, row_id: str) -> Optional[PendingAdminModel]:
        try:
            return _to_pydantic(PendingAdmin.get(PendingAdmin.id == row_id))
        except DoesNotExist:
            return None

    def get_by_token(self, token: str) -> Optional[PendingAdminModel]:
        if not token:
            return None
        try:
            return _to_pydantic(PendingAdmin.get(PendingAdmin.token == token))
        except DoesNotExist:
            return None

    def list_pending_for_email(
        self,
        email: str,
        *,
        kind: Optional[str] = None,
    ) -> List[PendingAdminModel]:
        """Return every active (``status='pending'`` and unexpired) pending
        row for the email.

        Filter ``kind`` lets callers ask for "only space invites" (the
        picker / accept UX) or "only superadmin legacy" (the resolver's
        silent-consume path).
        """
        q = PendingAdmin.select().where(
            (PendingAdmin.email == email.lower())
            & (PendingAdmin.status == STATUS_PENDING)
        )
        if kind is not None:
            q = q.where(PendingAdmin.kind == kind)
        out: List[PendingAdminModel] = []
        for r in q:
            if _expired(r):
                continue
            out.append(_to_pydantic(r))
        return out

    def get_active_by_email(
        self,
        email: str,
        *,
        kind: Optional[str] = None,
    ) -> Optional[PendingAdminModel]:
        """Backward-compat: return the first active pending row for the
        email. Prefer ``list_pending_for_email`` for new code — an email
        may now hold multiple pending invites simultaneously.
        """
        rows = self.list_pending_for_email(email, kind=kind)
        return rows[0] if rows else None

    def list_pending(self) -> List[PendingAdminModel]:
        rows = (
            PendingAdmin
            .select()
            .where(PendingAdmin.status == STATUS_PENDING)
            .order_by(PendingAdmin.created_at.desc())
        )
        return [_to_pydantic(r) for r in rows]

    def list_all(self) -> List[PendingAdminModel]:
        rows = (
            PendingAdmin
            .select()
            .order_by(PendingAdmin.created_at.desc())
        )
        return [_to_pydantic(r) for r in rows]

    def consume(self, email: str, *, by_admin_id: str) -> bool:
        """Legacy path: mark all pending rows for ``email`` consumed.

        Kept for the resolver's silent-apply branch on
        ``superadmin_legacy`` rows. ``mark_accepted`` is the new-style
        equivalent for a single row.
        """
        now = _now()
        rows = (
            PendingAdmin.update(
                consumed_at=now,
                consumed_by=by_admin_id,
                status=STATUS_ACCEPTED,
                responded_at=now,
                responded_by=by_admin_id,
            )
            .where(
                (PendingAdmin.email == email.lower())
                & (PendingAdmin.status == STATUS_PENDING)
            )
            .execute()
        )
        return rows > 0

    def mark_accepted(self, row_id: str, by_admin_id: str) -> bool:
        now = _now()
        rows = (
            PendingAdmin.update(
                status=STATUS_ACCEPTED,
                responded_at=now,
                responded_by=by_admin_id,
                consumed_at=now,
                consumed_by=by_admin_id,
            )
            .where(
                (PendingAdmin.id == row_id)
                & (PendingAdmin.status == STATUS_PENDING)
            )
            .execute()
        )
        return rows > 0

    def mark_rejected(self, row_id: str, by_admin_id: str) -> bool:
        rows = (
            PendingAdmin.update(
                status=STATUS_REJECTED,
                responded_at=_now(),
                responded_by=by_admin_id,
            )
            .where(
                (PendingAdmin.id == row_id)
                & (PendingAdmin.status == STATUS_PENDING)
            )
            .execute()
        )
        return rows > 0

    def mark_revoked(self, row_id: str, by_admin_id: Optional[str]) -> bool:
        rows = (
            PendingAdmin.update(
                status=STATUS_REVOKED,
                responded_at=_now(),
                responded_by=by_admin_id,
            )
            .where(
                (PendingAdmin.id == row_id)
                & (PendingAdmin.status == STATUS_PENDING)
            )
            .execute()
        )
        return rows > 0

    def mark_expired_sweep(self) -> int:
        """Promote any ``pending`` row whose ``expires_at`` is in the past
        to ``status='expired'``. Idempotent. Returns the row count."""
        now = datetime.utcnow()
        return (
            PendingAdmin.update(status=STATUS_EXPIRED, responded_at=_now())
            .where(
                (PendingAdmin.status == STATUS_PENDING)
                & (PendingAdmin.expires_at.is_null(False))
                & (PendingAdmin.expires_at < now)
            )
            .execute()
        )

    def revoke(self, email: str) -> bool:
        """Legacy path: hard-delete pending rows by email.

        Retained for the existing superadmin "delete /admins/pending/{email}"
        endpoint. New-style revoke uses ``mark_revoked`` against a token.
        """
        rows = PendingAdmin.delete().where(PendingAdmin.email == email.lower()).execute()
        return rows > 0


PendingAdmins = PendingAdminsTable(DB)
