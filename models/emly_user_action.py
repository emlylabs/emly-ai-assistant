import uuid
from datetime import datetime
from typing import Dict, Optional

import peewee as pw
from peewee import fn
from pydantic import BaseModel

from db.db import DB, JSONField
from models.bots import Bot
from models.emly_messages import EMLYMessage
from models.emly_users import EMLYUser


class EMLYUserActions(pw.Model):
    id = pw.CharField(max_length=255, primary_key=True)
    bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="user_actions")
    user = pw.ForeignKeyField(EMLYUser, field="id", on_delete="CASCADE", backref="actions")
    session_id = pw.CharField(max_length=255, null=True)
    message = pw.ForeignKeyField(
        EMLYMessage, field="id", on_delete="CASCADE", null=True, backref="actions"
    )
    action_name = pw.CharField(max_length=255, null=True)
    action_value = pw.CharField(max_length=255, null=True)
    created_on = pw.DateTimeField()
    updated_on = pw.DateTimeField()
    action_payload = JSONField(null=True)

    def to_dict(self, include_relations=True):
        action_dict = {
            "id": str(self.id),
            "bot_id": self.bot_id,
            "session_id": self.session_id,
            "action_name": self.action_name,
            "action_value": self.action_value,
            "created_on": self.created_on.isoformat() if self.created_on else None,
            "updated_on": self.updated_on.isoformat() if self.updated_on else None,
            "action_payload": self.action_payload,
        }

        if include_relations:
            action_dict["user"] = {
                "id": str(self.user.id),
                "first_name": self.user.first_name,
                "last_name": self.user.last_name,
                "email": self.user.email,
                "phone": self.user.phone,
                "ip": self.user.ip,
                "browser": self.user.browser,
                "meta": self.user.meta,
                "created_on": self.created_on.isoformat() if self.user.created_on else None,
                "updated_on": self.user.updated_on.isoformat() if self.user.updated_on else None,
            }

            if self.message:
                action_dict["message"] = {
                    "id": str(self.message.id),
                    "user_id": self.message.user_id,
                    "session_id": self.message.session_id,
                    "message": self.message.message,
                    "role": self.message.role,
                    "created_on": self.message.created_on.isoformat() if self.message.created_on else None,
                    "updated_on": self.message.updated_on.isoformat() if self.message.updated_on else None,
                    "not_useful": self.message.not_useful,
                }
            else:
                action_dict["message"] = None

        return action_dict

    class Meta:
        database = DB
        table_name = "emly_user_actions"


class EMLYUserActionsForm(BaseModel):
    bot_id: str
    user_id: str
    session_id: Optional[str] = None
    message_id: Optional[int] = None
    action_name: str
    action_value: str
    created_on: datetime
    updated_on: datetime
    action_payload: Optional[dict] = None


class EMLYUserActionsFormData(BaseModel):
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[int] = None
    action_name: Optional[str] = None
    action_value: Optional[str] = None
    action_payload: Optional[dict] = None


class EMLYUserActionsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([EMLYUserActions])

    def insert_new_action(
        self,
        bot_id: str,
        user_id: str,
        action_name: str,
        action_value: str,
        session_id: Optional[str] = None,
        message_id: Optional[int] = None,
        action_payload: Optional[dict] = None,
    ) -> Optional[dict]:
        now = datetime.now()
        row = EMLYUserActions.create(
            id=str(uuid.uuid4()),
            bot=bot_id,
            user=user_id,
            session_id=session_id,
            message=message_id,
            action_name=action_name,
            action_value=action_value,
            created_on=now,
            updated_on=now,
            action_payload=action_payload,
        )
        return row.to_dict() if row else None

    def submission_counts(self, bot_id: str, user_id: str) -> Dict[str, int]:
        """Count form submissions per ``action_value`` for one visitor.

        Powers the widget's "N of M submissions remaining" footnote and
        the post-limit engagement bubble. Bot-scoped: never crosses the
        tenant boundary."""
        rows = (
            EMLYUserActions
            .select(
                EMLYUserActions.action_value,
                fn.COUNT(EMLYUserActions.id).alias("c"),
            )
            .where(
                (EMLYUserActions.bot == bot_id)
                & (EMLYUserActions.user == user_id)
                & (EMLYUserActions.action_name == "form_submit")
                & (EMLYUserActions.action_value.is_null(False))
            )
            .group_by(EMLYUserActions.action_value)
        )
        return {r.action_value: int(r.c) for r in rows}


USER_ACTIONS = EMLYUserActionsTable(DB)
