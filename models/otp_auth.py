"""Widget one-time passcodes.

The ``otp`` column stores an HMAC of the code, never the code itself —
hashing and comparison live in ``services.otp``. This module only does
storage, with the attempt counter and single-use consumption implemented
as conditional UPDATE/DELETE statements so concurrent requests can't
race past them.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import peewee as pw
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB
from models.bots import Bot


def _utcnow() -> datetime:
    # Naive UTC: stored and compared consistently on SQLite and Postgres.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OtpAuth(pw.Model):
    id = pw.CharField(max_length=255, primary_key=True)
    bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="otp_auths")
    user_id = pw.CharField(max_length=255)
    otp_type = pw.CharField(max_length=255)
    # HMAC-SHA256 hex digest of the code (see services.otp.hash_code).
    otp = pw.CharField(max_length=255)
    expires_in = pw.BigIntegerField()  # minutes, as configured when issued
    authorized = pw.BooleanField()
    created_at = pw.DateTimeField()
    updated_at = pw.DateTimeField()
    # 009_otp_hardening
    expires_at = pw.DateTimeField(null=True)
    attempts = pw.IntegerField(default=0)

    class Meta:
        database = DB
        table_name = "otp_auth"
        indexes = ((("bot", "user_id"), True),)


class OtpAuthForm(BaseModel):
    bot_id: str
    user_id: str
    otp_type: str
    otp: str
    expires_in: int
    authorized: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class OtpAuthTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([OtpAuth])

    def upsert_otp(
        self,
        bot_id: str,
        user_id: str,
        otp_type: str,
        otp_hash: str,
        expires_in: int,
    ) -> dict:
        """Issue a code for (bot, user), replacing any outstanding one and
        resetting the attempt counter and expiry."""
        now = _utcnow()
        expires_at = now + timedelta(minutes=expires_in)
        with self.db.atomic():
            updated = (
                OtpAuth.update(
                    otp=otp_hash,
                    otp_type=otp_type,
                    expires_in=expires_in,
                    expires_at=expires_at,
                    attempts=0,
                    authorized=False,
                    updated_at=now,
                )
                .where((OtpAuth.bot == bot_id) & (OtpAuth.user_id == user_id))
                .execute()
            )
            if not updated:
                OtpAuth.create(
                    id=str(uuid.uuid4()),
                    bot=bot_id,
                    user_id=user_id,
                    otp_type=otp_type,
                    otp=otp_hash,
                    expires_in=expires_in,
                    expires_at=expires_at,
                    attempts=0,
                    authorized=False,
                    created_at=now,
                    updated_at=now,
                )
        return self.get_otp(bot_id, user_id)

    def get_otp(self, bot_id: str, user_id: str) -> Optional[dict]:
        try:
            row = OtpAuth.get((OtpAuth.bot == bot_id) & (OtpAuth.user_id == user_id))
            return model_to_dict(row, recurse=False)
        except pw.DoesNotExist:
            return None

    def claim_attempt(self, row_id: str, max_attempts: int) -> bool:
        """Atomically consume one verification attempt. False when the
        code has already used up ``max_attempts``."""
        return bool(
            OtpAuth.update(attempts=OtpAuth.attempts + 1)
            .where((OtpAuth.id == row_id) & (OtpAuth.attempts < max_attempts))
            .execute()
        )

    def delete_by_id(self, row_id: str) -> bool:
        """Delete one code. True only for the caller that actually removed
        it — used to make successful verification single-use."""
        return bool(OtpAuth.delete().where(OtpAuth.id == row_id).execute())


OtpAuthorization = OtpAuthTable(DB)
