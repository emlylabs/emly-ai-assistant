"""``local_credential`` table — passwords for the embedded OIDC issuer.

Used only when ``AUTH_LOCAL_ISSUER_ENABLED=true``. Each admin in ``admin_user``
has at most one row here.

Hashes are argon2id (``services/auth/issuer/passwords.py``).

Note on the FK: we deliberately store ``admin_id`` as a plain ``CharField``
rather than a ``ForeignKeyField`` to keep this table independent of the
``admin_user`` reshape happening in Phase 3 (drop-and-recreate). The Peewee
model in Phase 3 will add the FK back once ``admin_user`` has stabilised.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    DoesNotExist,
    IntegerField,
    Model,
)
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB

log = logging.getLogger(__name__)


class LocalCredential(Model):
    admin_id = CharField(max_length=64, primary_key=True)
    password_hash = CharField(max_length=512)
    password_set_at = DateTimeField()
    must_change = BooleanField(default=False)
    failed_attempts = IntegerField(default=0)
    locked_until = DateTimeField(null=True)

    class Meta:
        database = DB
        table_name = "local_credential"


class LocalCredentialModel(BaseModel):
    admin_id: str
    password_set_at: datetime
    must_change: bool
    failed_attempts: int
    locked_until: Optional[datetime] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_pydantic(row: LocalCredential) -> LocalCredentialModel:
    data = model_to_dict(row, recurse=False)
    return LocalCredentialModel(
        admin_id=data["admin_id"],
        password_set_at=data["password_set_at"],
        must_change=data["must_change"],
        failed_attempts=data["failed_attempts"],
        locked_until=data["locked_until"],
    )


class LocalCredentialsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([LocalCredential])

    def upsert(
        self,
        admin_id: str,
        password_hash: str,
        *,
        must_change: bool = False,
    ) -> LocalCredentialModel:
        now = _now()
        existing = self.get(admin_id)
        if existing is None:
            LocalCredential.create(
                admin_id=admin_id,
                password_hash=password_hash,
                password_set_at=now,
                must_change=must_change,
                failed_attempts=0,
                locked_until=None,
            )
        else:
            LocalCredential.update(
                password_hash=password_hash,
                password_set_at=now,
                must_change=must_change,
                failed_attempts=0,
                locked_until=None,
            ).where(LocalCredential.admin_id == admin_id).execute()
        return self.get(admin_id)  # type: ignore[return-value]

    def get(self, admin_id: str) -> Optional[LocalCredentialModel]:
        try:
            row = LocalCredential.get(LocalCredential.admin_id == admin_id)
            return _to_pydantic(row)
        except DoesNotExist:
            return None

    def get_password_hash(self, admin_id: str) -> Optional[str]:
        try:
            row = LocalCredential.get(LocalCredential.admin_id == admin_id)
            return row.password_hash
        except DoesNotExist:
            return None

    def delete(self, admin_id: str) -> bool:
        rows = LocalCredential.delete().where(LocalCredential.admin_id == admin_id).execute()
        return rows > 0

    def record_failure(self, admin_id: str, *, lockout_threshold: int, lockout_duration_seconds: int) -> Optional[LocalCredentialModel]:
        """Increment ``failed_attempts``; if it crosses the threshold, set ``locked_until``."""
        try:
            row = LocalCredential.get(LocalCredential.admin_id == admin_id)
        except DoesNotExist:
            return None
        new_count = row.failed_attempts + 1
        new_locked = row.locked_until
        if new_count >= lockout_threshold:
            from datetime import timedelta
            new_locked = _now() + timedelta(seconds=lockout_duration_seconds)
        LocalCredential.update(
            failed_attempts=new_count, locked_until=new_locked
        ).where(LocalCredential.admin_id == admin_id).execute()
        return self.get(admin_id)

    def record_success(self, admin_id: str) -> bool:
        rows = (
            LocalCredential.update(failed_attempts=0, locked_until=None)
            .where(LocalCredential.admin_id == admin_id)
            .execute()
        )
        return rows > 0

    def is_locked(self, admin_id: str) -> bool:
        try:
            row = LocalCredential.get(LocalCredential.admin_id == admin_id)
        except DoesNotExist:
            return False
        if row.locked_until is None:
            return False
        return row.locked_until > _now()

    def list_all(self) -> List[LocalCredentialModel]:
        return [_to_pydantic(r) for r in LocalCredential.select()]


LocalCredentials = LocalCredentialsTable(DB)
