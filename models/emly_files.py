import uuid
from datetime import datetime
from typing import List, Optional

import peewee as pw
from pydantic import BaseModel

from db.db import DB
from models.bots import Bot
from models.emly_users import EMLYUser


# Embedding lifecycle states. Phase 5 will surface these in the admin UI.
EMBEDDING_STATUS_PENDING = "pending"
EMBEDDING_STATUS_EMBEDDING = "embedding"
EMBEDDING_STATUS_EMBEDDED = "embedded"
EMBEDDING_STATUS_FAILED = "failed"

# Document type vocabulary. Stored as a free string so admins could extend
# without a migration, but the UI restricts new values to this set. Used
# in chunk metadata so the chat surface can label sources in citations.
DOCUMENT_TYPES = (
    "web_page",
    "document",
    "product",
    "support_article",
    "faq",
    "other",
)
DEFAULT_DOCUMENT_TYPE = "document"


class EMLYFiles(pw.Model):
    id = pw.CharField(max_length=255, primary_key=True)
    bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="files")
    user = pw.ForeignKeyField(EMLYUser, field="id", on_delete="CASCADE", null=True, backref="files")
    file_name = pw.CharField(max_length=255, null=True)
    file_type = pw.CharField(max_length=255, null=True)
    file_size = pw.IntegerField(null=True)
    size_bytes = pw.BigIntegerField(null=True)
    mime_type = pw.CharField(max_length=255, null=True)
    sha256 = pw.CharField(max_length=64, null=True)
    embedding_status = pw.CharField(max_length=32, default=EMBEDDING_STATUS_PENDING)
    error_message = pw.TextField(null=True)
    document_type = pw.CharField(max_length=64, default=DEFAULT_DOCUMENT_TYPE)
    created_on = pw.DateTimeField()
    updated_on = pw.DateTimeField()

    class Meta:
        database = DB
        table_name = "emly_files"


class EMLYFilesForm(BaseModel):
    bot_id: str
    user_id: Optional[str] = None
    file_name: str
    file_type: str
    file_size: int
    size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    sha256: Optional[str] = None
    embedding_status: str = EMBEDDING_STATUS_PENDING


class EMLYFilesTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([EMLYFiles])

    def insert_new_file(
        self,
        bot_id: str,
        file_name: str,
        file_type: str,
        file_size: int,
        user_id: Optional[str] = None,
        size_bytes: Optional[int] = None,
        mime_type: Optional[str] = None,
        sha256: Optional[str] = None,
        id: Optional[str] = None,
        document_type: str = DEFAULT_DOCUMENT_TYPE,
    ) -> Optional[dict]:
        now = datetime.now()
        row = EMLYFiles.create(
            id=id or str(uuid.uuid4()),
            bot=bot_id,
            user=user_id,
            file_name=file_name,
            file_type=file_type,
            file_size=file_size,
            size_bytes=size_bytes,
            mime_type=mime_type,
            sha256=sha256,
            embedding_status=EMBEDDING_STATUS_PENDING,
            error_message=None,
            document_type=document_type,
            created_on=now,
            updated_on=now,
        )
        return _row_to_dict(row)

    def update_document_type(self, bot_id: str, file_id: str, document_type: str) -> bool:
        rows = (
            EMLYFiles.update(document_type=document_type, updated_on=datetime.now())
            .where((EMLYFiles.id == file_id) & (EMLYFiles.bot == bot_id))
            .execute()
        )
        return rows > 0

    def list_for_bot(self, bot_id: str) -> List[dict]:
        rows = EMLYFiles.select().where(EMLYFiles.bot == bot_id).order_by(EMLYFiles.created_on.desc())
        return [_row_to_dict(r) for r in rows]

    def get_by_id(self, bot_id: str, file_id: str) -> Optional[dict]:
        try:
            row = EMLYFiles.get((EMLYFiles.id == file_id) & (EMLYFiles.bot == bot_id))
            return _row_to_dict(row)
        except pw.DoesNotExist:
            return None

    def update_status(
        self,
        bot_id: str,
        file_id: str,
        embedding_status: str,
        error_message: Optional[str] = None,
    ) -> bool:
        rows = (
            EMLYFiles.update(
                embedding_status=embedding_status,
                error_message=error_message,
                updated_on=datetime.now(),
            )
            .where((EMLYFiles.id == file_id) & (EMLYFiles.bot == bot_id))
            .execute()
        )
        return rows > 0

    def delete_by_id(self, bot_id: str, file_id: str) -> bool:
        rows = (
            EMLYFiles.delete()
            .where((EMLYFiles.id == file_id) & (EMLYFiles.bot == bot_id))
            .execute()
        )
        return rows > 0


def _row_to_dict(row: EMLYFiles) -> dict:
    return {
        "id": str(row.id),
        "bot_id": row.bot_id,
        "user_id": str(row.user.id) if row.user else None,
        "file_name": row.file_name,
        "file_type": row.file_type,
        "file_size": row.file_size,
        "size_bytes": row.size_bytes,
        "mime_type": row.mime_type,
        "sha256": row.sha256,
        "embedding_status": row.embedding_status,
        "error_message": row.error_message,
        "document_type": row.document_type,
        "created_on": row.created_on.isoformat() if row.created_on else None,
        "updated_on": row.updated_on.isoformat() if row.updated_on else None,
    }


Emly_Files = EMLYFilesTable(DB)
