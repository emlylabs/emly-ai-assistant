"""Workspace-wide defaults — one row keyed by ``id='singleton'``.

Holds the cross-bot knobs in §6 of ``improve-ux.md``: default theme
tokens, default LLM provider, default limits. Read by anyone with admin
access; written by superadmins via ``PUT /api/admin/workspace/settings``.
The runtime doesn't read this table — bots still resolve config from
their own ``config_json``. Workspace defaults inform new-bot creation
and surface inline in the per-bot editor as "Using workspace default".
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from peewee import CharField, DateTimeField, DoesNotExist, Model, TextField
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB

log = logging.getLogger(__name__)

SINGLETON_ID = "singleton"


class WorkspaceSettings(Model):
    id = CharField(max_length=64, primary_key=True)
    settings_json = TextField(null=True)
    updated_at = DateTimeField()
    updated_by_admin_id = CharField(max_length=64, null=True)

    class Meta:
        database = DB
        table_name = "workspace_settings"


class WorkspaceSettingsModel(BaseModel):
    settings: Dict[str, Any]
    updated_at: datetime
    updated_by_admin_id: Optional[str] = None


class WorkspaceSettingsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([WorkspaceSettings])

    def get(self) -> WorkspaceSettingsModel:
        try:
            row = WorkspaceSettings.get(WorkspaceSettings.id == SINGLETON_ID)
            raw = row.settings_json or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, ValueError):
                parsed = {}
            return WorkspaceSettingsModel(
                settings=parsed if isinstance(parsed, dict) else {},
                updated_at=row.updated_at,
                updated_by_admin_id=row.updated_by_admin_id,
            )
        except DoesNotExist:
            # No row yet — return an empty default. The first PUT will
            # insert; we don't lazy-write on read because the read path
            # is permission-light (any admin can read).
            return WorkspaceSettingsModel(
                settings={},
                updated_at=datetime.utcnow(),
                updated_by_admin_id=None,
            )

    def put(
        self,
        settings: Dict[str, Any],
        *,
        admin_id: Optional[str] = None,
    ) -> WorkspaceSettingsModel:
        now = datetime.utcnow()
        try:
            row = WorkspaceSettings.get(WorkspaceSettings.id == SINGLETON_ID)
            row.settings_json = json.dumps(settings)
            row.updated_at = now
            row.updated_by_admin_id = admin_id
            row.save()
        except DoesNotExist:
            WorkspaceSettings.create(
                id=SINGLETON_ID,
                settings_json=json.dumps(settings),
                updated_at=now,
                updated_by_admin_id=admin_id,
            )
        return self.get()


WorkspaceSettingsStore = WorkspaceSettingsTable(DB)
