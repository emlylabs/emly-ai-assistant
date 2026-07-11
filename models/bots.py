"""Bot model — root of the multi-tenant tree.

A bot is what the platform sells: one configurable AI agent. Every other
user-data table FKs to this row. Bots are created from the admin UI.
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
    IntegerField,
    Model,
    TextField,
)
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB

log = logging.getLogger(__name__)


class Bot(Model):
    id = CharField(max_length=255, primary_key=True)
    slug = CharField(max_length=255, unique=True)
    name = CharField(max_length=255)
    is_active = BooleanField(default=True)
    is_deleted = BooleanField(default=False)
    config_json = TextField(null=True)
    config_schema_version = IntegerField(default=1)
    config_version = IntegerField(default=0)
    api_key_encrypted = TextField(null=True)
    embed_model_id = CharField(max_length=255, null=True)
    current_owner_count = IntegerField(default=0)
    # Widget HMAC key versioning. Bumped via ``Bots.rotate_widget_key`` to
    # invalidate live widget tokens after a leak; the verifier accepts the
    # previous version for ``WIDGET_TOKEN_ROTATION_GRACE_SECONDS``.
    widget_key_version = IntegerField(default=1)
    widget_key_rotated_at = DateTimeField(null=True)
    created_at = DateTimeField()
    updated_at = DateTimeField()
    deleted_at = DateTimeField(null=True)

    class Meta:
        database = DB
        table_name = "bots"


class BotModel(BaseModel):
    id: str
    slug: str
    name: str
    is_active: bool
    is_deleted: bool
    config_json: Optional[Dict[str, Any]] = None
    config_schema_version: int
    config_version: int = 0
    api_key_encrypted: Optional[str] = None
    embed_model_id: Optional[str] = None
    current_owner_count: int
    widget_key_version: int = 1
    widget_key_rotated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None


def _row_to_model(row: Bot) -> BotModel:
    data = model_to_dict(row)
    if data.get("config_json"):
        try:
            data["config_json"] = json.loads(data["config_json"])
        except (TypeError, ValueError):
            data["config_json"] = None
    return BotModel(**data)


class BotsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([Bot])

    def insert(
        self,
        id: str,
        slug: str,
        name: str,
        config_json: Optional[Dict[str, Any]] = None,
        api_key_encrypted: Optional[str] = None,
        embed_model_id: Optional[str] = None,
        config_schema_version: int = 1,
    ) -> BotModel:
        now = datetime.utcnow()
        Bot.create(
            id=id,
            slug=slug,
            name=name,
            is_active=True,
            is_deleted=False,
            config_json=json.dumps(config_json) if config_json is not None else None,
            config_schema_version=config_schema_version,
            api_key_encrypted=api_key_encrypted,
            embed_model_id=embed_model_id,
            current_owner_count=0,
            created_at=now,
            updated_at=now,
        )
        return self.get_by_id(id)  # type: ignore[return-value]

    def get_by_id(self, id: str) -> Optional[BotModel]:
        try:
            return _row_to_model(Bot.get(Bot.id == id))
        except DoesNotExist:
            return None

    def get_by_slug(self, slug: str) -> Optional[BotModel]:
        try:
            return _row_to_model(Bot.get(Bot.slug == slug))
        except DoesNotExist:
            return None

    def rotate_widget_key(self, id: str) -> Optional[BotModel]:
        """Bump ``widget_key_version`` and stamp ``widget_key_rotated_at``.

        Existing widget tokens signed with the previous version remain valid
        for the rotation grace window (see ``services/auth/widget_hmac``).
        """
        now = datetime.utcnow()
        row = (
            Bot.update(
                widget_key_version=Bot.widget_key_version + 1,
                widget_key_rotated_at=now,
                updated_at=now,
            )
            .where(Bot.id == id)
            .execute()
        )
        if row == 0:
            return None
        return self.get_by_id(id)

    def list_active(self) -> List[BotModel]:
        rows = (
            Bot.select()
            .where((Bot.is_active == True) & (Bot.is_deleted == False))  # noqa: E712
            .order_by(Bot.created_at)
        )
        return [_row_to_model(r) for r in rows]

    def update_config(self, id: str, config_json: Dict[str, Any]) -> bool:
        # ``config_version = config_version + 1`` is the cross-worker
        # invalidation signal (Phase 3.5). Any worker that has a cached
        # ``BotRuntime`` for this bot polls the row's version and rebuilds
        # when it goes stale.
        rows = (
            Bot.update(
                config_json=json.dumps(config_json),
                config_version=Bot.config_version + 1,
                updated_at=datetime.utcnow(),
            )
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def update_api_key(self, id: str, api_key_encrypted: Optional[str]) -> bool:
        rows = (
            Bot.update(api_key_encrypted=api_key_encrypted, updated_at=datetime.utcnow())
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def soft_delete(self, id: str) -> bool:
        now = datetime.utcnow()
        rows = (
            Bot.update(
                is_active=False,
                is_deleted=True,
                deleted_at=now,
                updated_at=now,
            )
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def count(self) -> int:
        return Bot.select().where(Bot.is_deleted == False).count()  # noqa: E712


Bots = BotsTable(DB)
