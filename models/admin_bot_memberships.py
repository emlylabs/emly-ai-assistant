"""Admin↔Bot membership with role.

Roles: ``owner`` / ``admin`` / ``viewer``. The membership-write helpers in
Phase 4 enforce last-owner protection — the schema doesn't, by design.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from peewee import CharField, DateTimeField, DoesNotExist, ForeignKeyField, Model
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB
from models.admin_users import AdminUser
from models.bots import Bot

log = logging.getLogger(__name__)


ROLES = ("owner", "admin", "viewer")


class AdminBotMembership(Model):
    id = CharField(max_length=255, primary_key=True)
    admin = ForeignKeyField(AdminUser, field="id", on_delete="CASCADE", backref="memberships")
    bot = ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="memberships")
    role = CharField(max_length=32)
    created_at = DateTimeField()
    updated_at = DateTimeField()

    class Meta:
        database = DB
        table_name = "admin_bot_membership"
        indexes = ((("admin", "bot"), True),)


class AdminBotMembershipModel(BaseModel):
    id: str
    admin_id: str
    bot_id: str
    role: str
    created_at: datetime
    updated_at: datetime


def _row_to_model(row: AdminBotMembership) -> AdminBotMembershipModel:
    data = model_to_dict(row, recurse=False)
    return AdminBotMembershipModel(
        id=data["id"],
        admin_id=data["admin"],
        bot_id=data["bot"],
        role=data["role"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


class AdminBotMembershipsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([AdminBotMembership])

    def grant(self, id: str, admin_id: str, bot_id: str, role: str) -> AdminBotMembershipModel:
        if role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        now = datetime.utcnow()
        AdminBotMembership.create(
            id=id,
            admin=admin_id,
            bot=bot_id,
            role=role,
            created_at=now,
            updated_at=now,
        )
        return _row_to_model(AdminBotMembership.get(AdminBotMembership.id == id))

    def revoke(self, admin_id: str, bot_id: str) -> bool:
        rows = (
            AdminBotMembership.delete()
            .where(
                (AdminBotMembership.admin == admin_id)
                & (AdminBotMembership.bot == bot_id)
            )
            .execute()
        )
        return rows > 0

    def get(self, admin_id: str, bot_id: str) -> Optional[AdminBotMembershipModel]:
        try:
            row = AdminBotMembership.get(
                (AdminBotMembership.admin == admin_id)
                & (AdminBotMembership.bot == bot_id)
            )
            return _row_to_model(row)
        except DoesNotExist:
            return None

    def list_for_admin(self, admin_id: str) -> List[AdminBotMembershipModel]:
        rows = AdminBotMembership.select().where(AdminBotMembership.admin == admin_id)
        return [_row_to_model(r) for r in rows]

    def list_for_bot(self, bot_id: str) -> List[AdminBotMembershipModel]:
        rows = AdminBotMembership.select().where(AdminBotMembership.bot == bot_id)
        return [_row_to_model(r) for r in rows]

    def count_owners(self, bot_id: str) -> int:
        return (
            AdminBotMembership.select()
            .where((AdminBotMembership.bot == bot_id) & (AdminBotMembership.role == "owner"))
            .count()
        )

    def update_role(self, admin_id: str, bot_id: str, role: str) -> bool:
        if role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        rows = (
            AdminBotMembership.update(role=role, updated_at=datetime.utcnow())
            .where(
                (AdminBotMembership.admin == admin_id)
                & (AdminBotMembership.bot == bot_id)
            )
            .execute()
        )
        return rows > 0


AdminBotMemberships = AdminBotMembershipsTable(DB)
