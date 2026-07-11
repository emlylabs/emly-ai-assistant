"""Cross-channel identity mapping (opt-in).

Default identity is **siloed per channel** — each channel namespaces its
own ``emly_user_id``. This table is only populated when an admin enables
unified end-user identity for a bot (Phase 4+ feature). Until then it
exists in the schema as the place future linkage data will land, and as
the join target for cross-channel DSAR queries.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    DoesNotExist,
    ForeignKeyField,
    Model,
)
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB
from models.bot_channels import BotChannel
from models.bots import Bot
from models.emly_users import EMLYUser

log = logging.getLogger(__name__)


class EMLYUserChannelIdentity(Model):
    id = CharField(max_length=255, primary_key=True)
    bot = ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="channel_identities")
    channel = ForeignKeyField(BotChannel, field="id", on_delete="CASCADE", backref="identities")
    external_id = CharField(max_length=255)
    emly_user = ForeignKeyField(EMLYUser, field="id", on_delete="CASCADE", backref="channel_identities")
    verified = BooleanField(default=False)
    created_at = DateTimeField()

    class Meta:
        database = DB
        table_name = "emly_user_channel_identity"
        indexes = ((("channel", "external_id"), True),)


class EMLYUserChannelIdentityModel(BaseModel):
    id: str
    bot_id: str
    channel_id: str
    external_id: str
    emly_user_id: str
    verified: bool
    created_at: datetime


def _row_to_model(row: EMLYUserChannelIdentity) -> EMLYUserChannelIdentityModel:
    data = model_to_dict(row, recurse=False)
    return EMLYUserChannelIdentityModel(
        id=data["id"],
        bot_id=data["bot"],
        channel_id=data["channel"],
        external_id=data["external_id"],
        emly_user_id=data["emly_user"],
        verified=data["verified"],
        created_at=data["created_at"],
    )


class EMLYUserChannelIdentitiesTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([EMLYUserChannelIdentity])

    def insert(
        self,
        id: str,
        bot_id: str,
        channel_id: str,
        external_id: str,
        emly_user_id: str,
        verified: bool = False,
    ) -> EMLYUserChannelIdentityModel:
        EMLYUserChannelIdentity.create(
            id=id,
            bot=bot_id,
            channel=channel_id,
            external_id=external_id,
            emly_user=emly_user_id,
            verified=verified,
            created_at=datetime.utcnow(),
        )
        return _row_to_model(EMLYUserChannelIdentity.get(EMLYUserChannelIdentity.id == id))

    def lookup(self, channel_id: str, external_id: str) -> Optional[EMLYUserChannelIdentityModel]:
        try:
            row = EMLYUserChannelIdentity.get(
                (EMLYUserChannelIdentity.channel == channel_id)
                & (EMLYUserChannelIdentity.external_id == external_id)
            )
            return _row_to_model(row)
        except DoesNotExist:
            return None


EMLYUserChannelIdentities = EMLYUserChannelIdentitiesTable(DB)
