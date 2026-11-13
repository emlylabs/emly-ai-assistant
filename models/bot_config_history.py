"""Append-only history of ``bots.config_json`` saves.

Each row is a frozen snapshot of the JSON config plus the version it
was saved at and who wrote it. Capped at 50 rows per bot — older
snapshots get pruned by ``BotConfigHistoryTable.snapshot`` after each
insert so the table doesn't grow unbounded for bots that get edited
hundreds of times.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from peewee import (
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

RETENTION_LIMIT = 50


class BotConfigHistory(Model):
    id = CharField(max_length=64, primary_key=True)
    bot_id = CharField(max_length=64, index=True)
    config_version = IntegerField()
    config_json = TextField()
    admin_id = CharField(max_length=64, null=True)
    admin_email = CharField(max_length=255, null=True)
    created_at = DateTimeField()

    class Meta:
        database = DB
        table_name = "bot_config_history"
        indexes = ((("bot_id", "config_version"), True),)


class BotConfigHistoryModel(BaseModel):
    id: str
    bot_id: str
    config_version: int
    config_json: Dict[str, Any]
    admin_id: Optional[str] = None
    admin_email: Optional[str] = None
    created_at: datetime


def _row_to_model(row: BotConfigHistory) -> BotConfigHistoryModel:
    data = model_to_dict(row)
    raw = data.get("config_json") or "{}"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = {}
    data["config_json"] = parsed
    return BotConfigHistoryModel(**data)


class BotConfigHistoryTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([BotConfigHistory])

    def snapshot(
        self,
        *,
        bot_id: str,
        config_version: int,
        config_json: Dict[str, Any],
        admin_id: Optional[str] = None,
        admin_email: Optional[str] = None,
    ) -> BotConfigHistoryModel:
        """Insert a snapshot row and prune anything beyond ``RETENTION_LIMIT``
        for the same bot. Snapshot writes are best-effort from the caller's
        perspective — wrap in try/except so a history-table failure can't
        block the actual config save."""
        row_id = f"cfg-{uuid.uuid4()}"
        BotConfigHistory.create(
            id=row_id,
            bot_id=bot_id,
            config_version=config_version,
            config_json=json.dumps(config_json),
            admin_id=admin_id,
            admin_email=admin_email,
            created_at=datetime.utcnow(),
        )
        try:
            keep_ids = [
                r.id
                for r in (
                    BotConfigHistory.select(BotConfigHistory.id)
                    .where(BotConfigHistory.bot_id == bot_id)
                    .order_by(BotConfigHistory.created_at.desc())
                    .limit(RETENTION_LIMIT)
                )
            ]
            if keep_ids:
                (
                    BotConfigHistory.delete()
                    .where(
                        (BotConfigHistory.bot_id == bot_id)
                        & (BotConfigHistory.id.not_in(keep_ids))
                    )
                    .execute()
                )
        except Exception:
            log.exception("config history pruning failed for bot=%s", bot_id)
        return self.get_by_id(row_id)  # type: ignore[return-value]

    def get_by_id(self, id: str) -> Optional[BotConfigHistoryModel]:
        try:
            return _row_to_model(BotConfigHistory.get(BotConfigHistory.id == id))
        except DoesNotExist:
            return None

    def list_for_bot(self, bot_id: str, limit: int = RETENTION_LIMIT) -> List[BotConfigHistoryModel]:
        rows = (
            BotConfigHistory.select()
            .where(BotConfigHistory.bot_id == bot_id)
            .order_by(BotConfigHistory.created_at.desc())
            .limit(max(1, min(limit, RETENTION_LIMIT)))
        )
        return [_row_to_model(r) for r in rows]


BotConfigHistories = BotConfigHistoryTable(DB)
