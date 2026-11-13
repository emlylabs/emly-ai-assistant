"""Admin-saved bot templates — workspace-scoped, listed by the create wizard.

Each row is a frozen snapshot of a real bot's ``config_json`` plus a
display name and description. The create wizard composes its picker
from the four code-baked templates plus these rows; selecting one calls
``POST /api/admin/bots`` with the snapshot as ``config_json``.

Deletion is hard. The bots that were created from a now-deleted
template keep their config — we don't FK back.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from peewee import CharField, DateTimeField, DoesNotExist, Model, TextField
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB

log = logging.getLogger(__name__)


class WorkspaceBotTemplate(Model):
    id = CharField(max_length=64, primary_key=True)
    name = CharField(max_length=255)
    description = CharField(max_length=512, null=True)
    config_json = TextField()
    created_by_admin_id = CharField(max_length=64, null=True)
    created_at = DateTimeField()
    updated_at = DateTimeField()

    class Meta:
        database = DB
        table_name = "workspace_bot_templates"


class WorkspaceBotTemplateModel(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    config_json: Dict[str, Any]
    created_by_admin_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


def _row_to_model(row: WorkspaceBotTemplate) -> WorkspaceBotTemplateModel:
    data = model_to_dict(row)
    raw = data.get("config_json") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        parsed = {}
    data["config_json"] = parsed if isinstance(parsed, dict) else {}
    return WorkspaceBotTemplateModel(**data)


class WorkspaceBotTemplatesTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([WorkspaceBotTemplate])

    def list(self) -> List[WorkspaceBotTemplateModel]:
        rows = WorkspaceBotTemplate.select().order_by(WorkspaceBotTemplate.created_at.desc())
        return [_row_to_model(r) for r in rows]

    def get_by_id(self, id: str) -> Optional[WorkspaceBotTemplateModel]:
        try:
            return _row_to_model(WorkspaceBotTemplate.get(WorkspaceBotTemplate.id == id))
        except DoesNotExist:
            return None

    def insert(
        self,
        id: str,
        *,
        name: str,
        config_json: Dict[str, Any],
        description: Optional[str] = None,
        created_by_admin_id: Optional[str] = None,
    ) -> WorkspaceBotTemplateModel:
        now = datetime.utcnow()
        WorkspaceBotTemplate.create(
            id=id,
            name=name,
            description=description,
            config_json=json.dumps(config_json),
            created_by_admin_id=created_by_admin_id,
            created_at=now,
            updated_at=now,
        )
        return self.get_by_id(id)  # type: ignore[return-value]

    def delete(self, id: str) -> bool:
        rows = WorkspaceBotTemplate.delete().where(WorkspaceBotTemplate.id == id).execute()
        return rows > 0


WorkspaceBotTemplates = WorkspaceBotTemplatesTable(DB)
