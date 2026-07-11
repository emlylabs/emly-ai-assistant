import logging
import time
from datetime import datetime
from typing import List, Optional

from peewee import (
    BigIntegerField,
    CharField,
    CompositeKey,
    DateTimeField,
    DoesNotExist,
    FloatField,
    ForeignKeyField,
    Model,
    TextField,
)
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB, JSONField
from models.bots import Bot

####################
# User DB Schema
####################


class EMLYUser(Model):
    # Primary key is the composite ``(bot_id, id)`` — see
    # ``Meta.primary_key`` below. ``id`` alone is no longer unique because
    # widgets cache user ids in localStorage and the same client-supplied
    # ``emly-<uuid>`` can legitimately appear under multiple bots embedded
    # on the same domain. Bot-scoped uniqueness reflects the tenancy
    # model: every other table in the schema is bot-scoped too.
    id = CharField(max_length=255)
    bot = ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="emly_users")
    first_name = CharField(max_length=255, null=True)
    last_name = CharField(max_length=255, null=True)
    email = CharField(max_length=255, null=True)
    phone = CharField(max_length=255, null=True)
    ip = CharField(max_length=255)
    browser = CharField(max_length=255)
    timestamp = BigIntegerField()
    country = CharField(max_length=255, null=True)
    city = CharField(max_length=255, null=True)
    region = TextField(null=True)
    latitude = FloatField(null=True)
    longitude = FloatField(null=True)
    created_on = DateTimeField(null=True)
    updated_on = DateTimeField(null=True)
    meta = JSONField(null=True)

    class Meta:
        database = DB
        table_name = "emly_user"
        primary_key = CompositeKey("bot", "id")


class EMLYUserModel(BaseModel):
    id: str
    bot_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    ip: str
    browser: str
    timestamp: int
    country: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    created_on: Optional[datetime] = None
    updated_on: Optional[datetime] = None
    meta: Optional[dict] = None


def _row_to_model(row: EMLYUser) -> EMLYUserModel:
    data = model_to_dict(row, recurse=False)
    return EMLYUserModel(
        id=data["id"],
        bot_id=data["bot"],
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
        email=data.get("email"),
        phone=data.get("phone"),
        ip=data["ip"],
        browser=data["browser"],
        timestamp=data["timestamp"],
        country=data.get("country"),
        city=data.get("city"),
        region=data.get("region"),
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
        created_on=data.get("created_on"),
        updated_on=data.get("updated_on"),
        meta=data.get("meta"),
    )


####################
# Forms
####################


class EMLYUserUpdateForm(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    meta: Optional[dict] = None
    country: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class EMLYUsersTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([EMLYUser])

    def insert_new_user(
        self,
        bot_id: str,
        id: str,
        first_name: Optional[str],
        last_name: Optional[str],
        email: Optional[str],
        phone: Optional[str],
        ip: str,
        browser: str,
        meta: Optional[dict],
        country: Optional[str] = None,
        city: Optional[str] = None,
        region: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> Optional[EMLYUserModel]:
        now = datetime.now()
        EMLYUser.create(
            id=id,
            bot=bot_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            ip=ip,
            browser=browser,
            timestamp=int(time.time()),
            country=country,
            city=city,
            region=region,
            latitude=latitude,
            longitude=longitude,
            created_on=now,
            updated_on=now,
            meta=meta,
        )
        return self.get_user_by_id(bot_id, id)

    def get_user_by_id(self, bot_id: str, id: str) -> Optional[EMLYUserModel]:
        try:
            row = EMLYUser.get((EMLYUser.id == id) & (EMLYUser.bot == bot_id))
            return _row_to_model(row)
        except DoesNotExist:
            return None
        except Exception:
            logging.exception("get_user_by_id failed for id=%s bot=%s", id, bot_id)
            raise

    def get_users(self, bot_id: str, skip: int = 0, limit: int = 50) -> List[EMLYUserModel]:
        query = (
            EMLYUser.select()
            .where(EMLYUser.bot == bot_id)
            .order_by(EMLYUser.created_on.desc())
            .offset(skip)
            .limit(limit)
        )
        return [_row_to_model(u) for u in query]

    def count(self, bot_id: str) -> int:
        return EMLYUser.select().where(EMLYUser.bot == bot_id).count()

    def update_user(
        self, bot_id: str, id: str, emly_user: EMLYUserUpdateForm
    ) -> Optional[EMLYUserModel]:
        try:
            (
                EMLYUser.update(
                    first_name=emly_user.first_name,
                    last_name=emly_user.last_name,
                    email=emly_user.email,
                    phone=emly_user.phone,
                    updated_on=datetime.now(),
                    meta=emly_user.meta,
                    country=emly_user.country,
                    city=emly_user.city,
                    region=emly_user.region,
                    latitude=emly_user.latitude,
                    longitude=emly_user.longitude,
                )
                .where((EMLYUser.id == id) & (EMLYUser.bot == bot_id))
                .execute()
            )
            return self.get_user_by_id(bot_id, id)
        except Exception as e:
            logging.exception("Exception occurred while updating the emly user: %s", e)
            return None


EMLYUsers = EMLYUsersTable(DB)
