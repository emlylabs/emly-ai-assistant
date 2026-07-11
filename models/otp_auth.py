import uuid
from datetime import datetime
from typing import Optional

import peewee as pw
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB
from models.bots import Bot


class OtpAuth(pw.Model):
    id = pw.CharField(max_length=255, primary_key=True)
    bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="otp_auths")
    user_id = pw.CharField(max_length=255)
    otp_type = pw.CharField(max_length=255)
    otp = pw.CharField(max_length=255)
    expires_in = pw.BigIntegerField()
    authorized = pw.BooleanField()
    created_on = pw.DateTimeField()
    updated_on = pw.DateTimeField()

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
    created_on: Optional[datetime] = None
    updated_on: Optional[datetime] = None


class OtpAuthTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([OtpAuth])

    def insert_otp(
        self,
        bot_id: str,
        user_id: str,
        otp_type: str,
        otp: str,
        expires_in: int,
        authorized: bool,
    ) -> Optional[dict]:
        now = datetime.now()
        row = OtpAuth.create(
            id=str(uuid.uuid4()),
            bot=bot_id,
            user_id=user_id,
            otp_type=otp_type,
            otp=otp,
            expires_in=expires_in,
            authorized=authorized,
            created_on=now,
            updated_on=now,
        )
        return model_to_dict(row, recurse=False) if row else None

    def get_otp(self, bot_id: str, user_id: str) -> Optional[dict]:
        try:
            row = OtpAuth.get((OtpAuth.bot == bot_id) & (OtpAuth.user_id == user_id))
            return model_to_dict(row, recurse=False)
        except pw.DoesNotExist:
            return None
        except Exception as e:
            raise Exception(f"An error occurred during the update: {str(e)}")

    def update_otp(
        self, bot_id: str, user_id: str, otp: str, expires_in: int
    ) -> Optional[dict]:
        try:
            (
                OtpAuth.update(
                    otp=otp, expires_in=expires_in, updated_on=datetime.now()
                )
                .where((OtpAuth.bot == bot_id) & (OtpAuth.user_id == user_id))
                .execute()
            )
            return self.get_otp(bot_id, user_id)
        except pw.DoesNotExist:
            return None
        except Exception as e:
            raise Exception(f"An error occurred during the update: {str(e)}")

    def validate_otp(self, bot_id: str, user_id: str, otp: str) -> bool:
        try:
            row = OtpAuth.get((OtpAuth.bot == bot_id) & (OtpAuth.user_id == user_id))
            now = datetime.now()
            expiry_time = now.timestamp() + (row.expires_in * 60)
            if now.timestamp() > expiry_time:
                return False
            return row.otp == otp
        except pw.DoesNotExist:
            return False
        except Exception as e:
            raise Exception(f"An error occurred during authorizing otp: {str(e)}")


OtpAuthorization = OtpAuthTable(DB)
