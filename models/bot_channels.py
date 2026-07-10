"""Bot channel — the surface a bot is exposed on.

A bot has many channels: web widget, Slack, Teams, Google Chat,
WhatsApp, Telegram. The channel-adapter dispatcher in
``channels/dispatcher.py`` resolves an inbound webhook to a row by
``(type, external_id)`` (payload-routed) or by primary key
(path-routed). ``credentials_encrypted`` holds the per-install bot
token / OAuth token set, Fernet-sealed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    DoesNotExist,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB
from models.bots import Bot

log = logging.getLogger(__name__)


CHANNEL_TYPES = ("web_widget", "google_chat", "slack", "teams", "telegram", "whatsapp_cloud")


class BotChannel(Model):
    id = CharField(max_length=255, primary_key=True)
    bot = ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="channels")
    type = CharField(max_length=64)
    external_id = CharField(max_length=255, null=True)
    credentials_encrypted = TextField(null=True)
    config_json = TextField(null=True)
    is_active = BooleanField(default=True)
    created_at = DateTimeField()
    secrets_rotated_at = DateTimeField(null=True)
    display_name = CharField(max_length=255, null=True)
    config_version = IntegerField(default=0)

    class Meta:
        database = DB
        table_name = "bot_channel"
        indexes = (
            (("type", "external_id"), True),
            (("bot",), False),
        )


class BotChannelModel(BaseModel):
    id: str
    bot_id: str
    type: str
    external_id: Optional[str] = None
    credentials_encrypted: Optional[str] = None
    config_json: Optional[Dict[str, Any]] = None
    is_active: bool
    created_at: datetime
    secrets_rotated_at: Optional[datetime] = None
    display_name: Optional[str] = None
    config_version: int = 0


def _row_to_model(row: BotChannel) -> BotChannelModel:
    data = model_to_dict(row, recurse=False)
    cfg = data.get("config_json")
    if cfg:
        try:
            cfg = json.loads(cfg)
        except (TypeError, ValueError):
            cfg = None
    return BotChannelModel(
        id=data["id"],
        bot_id=data["bot"],
        type=data["type"],
        external_id=data.get("external_id"),
        credentials_encrypted=data.get("credentials_encrypted"),
        config_json=cfg,
        is_active=data["is_active"],
        created_at=data["created_at"],
        secrets_rotated_at=data.get("secrets_rotated_at"),
        display_name=data.get("display_name"),
        config_version=data.get("config_version") or 0,
    )


class BotChannelsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([BotChannel])

    def insert(
        self,
        id: str,
        bot_id: str,
        type: str,
        external_id: Optional[str] = None,
        credentials_encrypted: Optional[str] = None,
        config_json: Optional[Dict[str, Any]] = None,
        display_name: Optional[str] = None,
    ) -> BotChannelModel:
        if type not in CHANNEL_TYPES:
            raise ValueError(f"unknown channel type: {type}")
        BotChannel.create(
            id=id,
            bot=bot_id,
            type=type,
            external_id=external_id,
            credentials_encrypted=credentials_encrypted,
            config_json=json.dumps(config_json) if config_json is not None else None,
            is_active=True,
            created_at=datetime.utcnow(),
            secrets_rotated_at=datetime.utcnow() if credentials_encrypted else None,
            display_name=display_name,
            config_version=0,
        )
        return _row_to_model(BotChannel.get(BotChannel.id == id))

    def get_by_id(self, id: str) -> Optional[BotChannelModel]:
        try:
            return _row_to_model(BotChannel.get(BotChannel.id == id))
        except DoesNotExist:
            return None

    def get_by_external(self, type: str, external_id: str) -> Optional[BotChannelModel]:
        try:
            row = BotChannel.get(
                (BotChannel.type == type) & (BotChannel.external_id == external_id)
            )
            return _row_to_model(row)
        except DoesNotExist:
            return None

    def list_for_bot(self, bot_id: str) -> List[BotChannelModel]:
        rows = BotChannel.select().where(BotChannel.bot == bot_id)
        return [_row_to_model(r) for r in rows]

    def list_active_by_type(self, type: str) -> List[BotChannelModel]:
        rows = BotChannel.select().where(
            (BotChannel.type == type) & (BotChannel.is_active == True)  # noqa: E712
        )
        return [_row_to_model(r) for r in rows]

    def get_default_web_widget_channel_id(self, bot_id: str) -> Optional[str]:
        """Return the id of the bot's first active web_widget channel, if any.

        Phase 3 backend-backfill: legacy widget chat ingress (`/emly/api/chat`)
        doesn't carry a channel id today, so we synthesize one by looking up
        the bot's web_widget BotChannel row at message-receive time. Returns
        ``None`` if the bot hasn't installed a web_widget channel — the
        ``channel_id`` column on `EMLYMessage` is nullable so this is fine.
        """
        try:
            row = (
                BotChannel.select()
                .where(
                    (BotChannel.bot == bot_id)
                    & (BotChannel.type == "web_widget")
                    & (BotChannel.is_active == True)  # noqa: E712
                )
                .order_by(BotChannel.created_at.asc())
                .first()
            )
            return row.id if row is not None else None
        except DoesNotExist:
            return None

    def update_credentials(self, id: str, credentials_encrypted: str) -> bool:
        rows = (
            BotChannel.update(
                credentials_encrypted=credentials_encrypted,
                secrets_rotated_at=datetime.utcnow(),
                config_version=BotChannel.config_version + 1,
            )
            .where(BotChannel.id == id)
            .execute()
        )
        return rows > 0

    def update_external_id(self, id: str, external_id: str, display_name: Optional[str] = None) -> bool:
        update_fields = {"external_id": external_id}
        if display_name is not None:
            update_fields["display_name"] = display_name
        rows = BotChannel.update(**update_fields).where(BotChannel.id == id).execute()
        return rows > 0

    def update_config(self, id: str, config_json: Dict[str, Any]) -> bool:
        rows = (
            BotChannel.update(config_json=json.dumps(config_json))
            .where(BotChannel.id == id)
            .execute()
        )
        return rows > 0

    def set_active(self, id: str, is_active: bool) -> bool:
        rows = BotChannel.update(is_active=is_active).where(BotChannel.id == id).execute()
        return rows > 0

    def delete(self, id: str) -> bool:
        rows = BotChannel.delete().where(BotChannel.id == id).execute()
        return rows > 0


BotChannels = BotChannelsTable(DB)
