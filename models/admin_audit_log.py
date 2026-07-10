"""``admin_audit_log`` Peewee model + reader helpers.

Writes go through ``services.audit.audit(...)``; reads go through
``AdminAuditLogTable.list_filtered`` (paginated, indexed).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    Model,
    TextField,
)
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB

log = logging.getLogger(__name__)


class AdminAuditLog(Model):
    id = CharField(max_length=64, primary_key=True)
    admin_id = CharField(max_length=64, null=True)
    bot_id = CharField(max_length=64, null=True)
    action = CharField(max_length=128)
    target_type = CharField(max_length=64, null=True)
    target_id = CharField(max_length=255, null=True)
    payload = TextField(null=True)  # JSON-encoded
    ip = CharField(max_length=64, null=True)
    ua = CharField(max_length=512, null=True)
    success = BooleanField(default=True)
    created_at = DateTimeField()

    class Meta:
        database = DB
        table_name = "admin_audit_log"


class AdminAuditLogModel(BaseModel):
    id: str
    admin_id: Optional[str] = None
    bot_id: Optional[str] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    payload: Optional[dict] = None
    ip: Optional[str] = None
    ua: Optional[str] = None
    success: bool
    created_at: datetime


def _row_to_model(row: AdminAuditLog) -> AdminAuditLogModel:
    data = model_to_dict(row, recurse=False)
    raw_payload = data.get("payload")
    parsed: Optional[dict] = None
    if raw_payload:
        try:
            parsed = json.loads(raw_payload)
        except (TypeError, ValueError):
            parsed = {"_raw": raw_payload}
    return AdminAuditLogModel(
        id=data["id"],
        admin_id=data["admin_id"],
        bot_id=data["bot_id"],
        action=data["action"],
        target_type=data["target_type"],
        target_id=data["target_id"],
        payload=parsed,
        ip=data["ip"],
        ua=data["ua"],
        success=data["success"],
        created_at=data["created_at"],
    )


class AdminAuditLogTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([AdminAuditLog])

    def insert(
        self,
        *,
        admin_id: Optional[str],
        bot_id: Optional[str],
        action: str,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        payload: Optional[dict] = None,
        ip: Optional[str] = None,
        ua: Optional[str] = None,
        success: bool = True,
    ) -> str:
        row_id = f"audit-{uuid.uuid4()}"
        AdminAuditLog.create(
            id=row_id,
            admin_id=admin_id,
            bot_id=bot_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=json.dumps(payload, default=str) if payload else None,
            ip=ip,
            ua=ua[:512] if ua else None,
            success=success,
            created_at=datetime.now(timezone.utc),
        )
        return row_id

    def list_filtered(
        self,
        *,
        admin_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        action: Optional[str] = None,
        success: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[AdminAuditLogModel]:
        q = AdminAuditLog.select().order_by(AdminAuditLog.created_at.desc())
        if admin_id is not None:
            q = q.where(AdminAuditLog.admin_id == admin_id)
        if bot_id is not None:
            q = q.where(AdminAuditLog.bot_id == bot_id)
        if action is not None:
            q = q.where(AdminAuditLog.action == action)
        if success is not None:
            q = q.where(AdminAuditLog.success == success)
        q = q.offset(skip).limit(min(max(limit, 1), 500))
        return [_row_to_model(r) for r in q]


AdminAuditLogs = AdminAuditLogTable(DB)
