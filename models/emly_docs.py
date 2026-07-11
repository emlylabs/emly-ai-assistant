"""Per-bot ingested-document index.

Mirrors the Qdrant points one-for-one — used for fast list/delete/dedupe
in the file-upload pipeline. Phase 5 may consolidate this with
``emly_files`` once the upload flow is fully bot-scoped.
"""
import logging
import traceback
import uuid
from datetime import datetime
from typing import List, Optional

import peewee as pw
from pydantic import BaseModel

from db.db import DB
from models.bots import Bot

logger = logging.getLogger("EMLYDocsModel")


class EMLYDocs(pw.Model):
    id = pw.CharField(max_length=255, primary_key=True)
    bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="docs")
    name = pw.CharField(max_length=255)
    content_hash = pw.TextField(null=True)
    created_on = pw.DateTimeField(null=True)
    updated_on = pw.DateTimeField(null=True)

    class Meta:
        database = DB
        table_name = "emly_docs"
        indexes = ((("bot", "name"), True),)


class EMLYDocsModel(BaseModel):
    id: str
    bot_id: str
    name: str
    content_hash: Optional[str] = None
    created_on: Optional[datetime] = None
    updated_on: Optional[datetime] = None


def _row_to_model(row: EMLYDocs) -> EMLYDocsModel:
    return EMLYDocsModel(
        id=str(row.id),
        bot_id=row.bot_id,
        name=row.name,
        content_hash=row.content_hash,
        created_on=row.created_on,
        updated_on=row.updated_on,
    )


class EMLYDocsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([EMLYDocs])

    def insert_new_emly_doc(
        self,
        bot_id: str,
        name: str,
        content_hash: Optional[str] = None,
    ) -> Optional[EMLYDocsModel]:
        now = datetime.now()
        row = EMLYDocs.create(
            id=str(uuid.uuid4()),
            bot=bot_id,
            name=name,
            content_hash=content_hash,
            created_on=now,
            updated_on=now,
        )
        return _row_to_model(row) if row else None

    def get_emly_doc_by_name(self, bot_id: str, name: str) -> Optional[EMLYDocsModel]:
        try:
            row = EMLYDocs.get((EMLYDocs.bot == bot_id) & (EMLYDocs.name == name))
            return _row_to_model(row)
        except pw.DoesNotExist:
            return None
        except Exception:
            logger.exception("get_emly_doc_by_name failed for bot=%s name=%s", bot_id, name)
            raise

    def list_for_bot(self, bot_id: str) -> List[EMLYDocsModel]:
        return [_row_to_model(r) for r in EMLYDocs.select().where(EMLYDocs.bot == bot_id)]

    def delete_emly_doc_list(self, bot_id: str, docs: List[str]) -> bool:
        try:
            for doc in docs:
                EMLYDocs.delete().where(
                    (EMLYDocs.bot == bot_id) & (EMLYDocs.name == doc)
                ).execute()
            logger.info("Documents %s deleted successfully for bot=%s", docs, bot_id)
            return True
        except Exception as e:
            logger.error("Error deleting documents %s: %s", docs, e)
            return False

    def delete_all_for_bot(self, bot_id: str) -> bool:
        try:
            EMLYDocs.delete().where(EMLYDocs.bot == bot_id).execute()
            return True
        except Exception as e:
            traceback.print_exception(e)
            return False

    def update_emly_doc_by_name(
        self, bot_id: str, name: str, fields_to_update: dict
    ) -> Optional[EMLYDocsModel]:
        try:
            row = EMLYDocs.get((EMLYDocs.bot == bot_id) & (EMLYDocs.name == name))
            for field, value in fields_to_update.items():
                lower = field.lower()
                if lower == "name":
                    row.name = value
                elif lower == "content_hash":
                    row.content_hash = value
            row.updated_on = datetime.now()
            row.save()
            return _row_to_model(row)
        except pw.DoesNotExist:
            logger.warning("EmlyDoc bot=%s name=%s not found for update", bot_id, name)
            return None
        except Exception:
            logger.exception("update_emly_doc_by_name failed bot=%s name=%s", bot_id, name)
            raise

    def delete_emly_doc_by_name(self, bot_id: str, name: str) -> bool:
        try:
            EMLYDocs.delete().where(
                (EMLYDocs.bot == bot_id) & (EMLYDocs.name == name)
            ).execute()
            return True
        except Exception as e:
            logger.error("Error deleting EmlyDoc bot=%s name=%s: %s", bot_id, name, e)
            return False


EmlyDocs = EMLYDocsTable(DB)
