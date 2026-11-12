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
    created_at = pw.DateTimeField()
    updated_at = pw.DateTimeField()

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
            created_at=now,
            updated_at=now,
        )
        return _row_to_dict(row)

    def update_document_type(self, bot_id: str, file_id: str, document_type: str) -> bool:
        rows = (
            EMLYFiles.update(document_type=document_type, updated_at=datetime.now())
            .where((EMLYFiles.id == file_id) & (EMLYFiles.bot == bot_id))
            .execute()
        )
        return rows > 0

    # Status buckets mapped to the underlying embedding_status values.
    # Used by both filtering and the status_counts aggregate so the UI
    # chips and the active filter stay in lockstep.
    _STATUS_BUCKETS = {
        "indexed": (EMBEDDING_STATUS_EMBEDDED,),
        "in_progress": (EMBEDDING_STATUS_PENDING, EMBEDDING_STATUS_EMBEDDING),
        "errors": (EMBEDDING_STATUS_FAILED,),
    }

    def _apply_filters(
        self,
        query,
        document_types: Optional[List[str]] = None,
        status_bucket: Optional[str] = None,
        q: Optional[str] = None,
    ):
        if document_types:
            query = query.where(EMLYFiles.document_type.in_(list(document_types)))
        if status_bucket and status_bucket in self._STATUS_BUCKETS:
            query = query.where(
                EMLYFiles.embedding_status.in_(list(self._STATUS_BUCKETS[status_bucket]))
            )
        if q:
            like = f"%{q}%"
            query = query.where(
                (EMLYFiles.file_name ** like) | (EMLYFiles.mime_type ** like)
            )
        return query

    def list_for_bot(
        self,
        bot_id: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        document_types: Optional[List[str]] = None,
        status_bucket: Optional[str] = None,
        q: Optional[str] = None,
    ) -> List[dict]:
        query = EMLYFiles.select().where(EMLYFiles.bot == bot_id)
        query = self._apply_filters(query, document_types, status_bucket, q)
        query = query.order_by(EMLYFiles.created_at.desc())
        if limit is not None:
            query = query.limit(limit)
        if offset:
            query = query.offset(offset)
        return [_row_to_dict(r) for r in query]

    def count_for_bot(
        self,
        bot_id: str,
        document_types: Optional[List[str]] = None,
        status_bucket: Optional[str] = None,
        q: Optional[str] = None,
    ) -> int:
        query = EMLYFiles.select().where(EMLYFiles.bot == bot_id)
        query = self._apply_filters(query, document_types, status_bucket, q)
        return query.count()

    def status_counts(
        self,
        bot_id: str,
        document_types: Optional[List[str]] = None,
    ) -> dict:
        """Return ``{indexed, in_progress, errors}`` counts for the bot.

        Counts are scoped to ``document_types`` but ignore any active
        status filter — that's so the UI chip labels can show absolute
        bucket sizes regardless of which bucket is selected.
        """
        from peewee import fn, Case

        bucket_expr = Case(
            EMLYFiles.embedding_status,
            (
                (EMBEDDING_STATUS_EMBEDDED, "indexed"),
                (EMBEDDING_STATUS_PENDING, "in_progress"),
                (EMBEDDING_STATUS_EMBEDDING, "in_progress"),
                (EMBEDDING_STATUS_FAILED, "errors"),
            ),
            None,
        )
        query = (
            EMLYFiles.select(bucket_expr.alias("bucket"), fn.COUNT(EMLYFiles.id).alias("c"))
            .where(EMLYFiles.bot == bot_id)
        )
        query = self._apply_filters(query, document_types=document_types)
        query = query.group_by(bucket_expr)
        out = {"indexed": 0, "in_progress": 0, "errors": 0}
        for row in query.dicts():
            bucket = row.get("bucket")
            if bucket in out:
                out[bucket] = int(row["c"])
        return out

    def document_type_counts(self, bot_id: str) -> dict:
        """Return ``{document_type: count}`` for the bot, unfiltered.

        Drives the Collections rail; we want absolute bucket sizes so the
        user can see "Documents (12)" even while filtering on FAQ.
        """
        from peewee import fn

        query = (
            EMLYFiles.select(EMLYFiles.document_type, fn.COUNT(EMLYFiles.id).alias("c"))
            .where(EMLYFiles.bot == bot_id)
            .group_by(EMLYFiles.document_type)
        )
        return {row["document_type"]: int(row["c"]) for row in query.dicts()}

    def last_updated_at(self, bot_id: str) -> Optional[str]:
        from peewee import fn

        row = (
            EMLYFiles.select(fn.MAX(EMLYFiles.updated_at).alias("ts"))
            .where(EMLYFiles.bot == bot_id)
            .dicts()
            .first()
        )
        ts = row.get("ts") if row else None
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts.isoformat()
        return str(ts)

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
                updated_at=datetime.now(),
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

    def storage_summary(self, bot_id: str) -> tuple[int, int]:
        """Return ``(file_count, total_size_bytes)`` for a bot.

        Used by the workspace summary endpoint so the overview table can
        show storage usage without N+1 fetching the file list per bot.
        ``size_bytes`` is preferred over ``file_size`` (the canonical
        BigInt column; ``file_size`` is the legacy Int).
        """
        # Single round-trip aggregate. COALESCE keeps null-only bots at 0
        # rather than returning None and forcing the caller to coerce.
        from peewee import fn

        row = (
            EMLYFiles.select(
                fn.COUNT(EMLYFiles.id).alias("c"),
                fn.COALESCE(fn.SUM(EMLYFiles.size_bytes), 0).alias("s"),
            )
            .where(EMLYFiles.bot == bot_id)
            .first()
        )
        if row is None:
            return 0, 0
        return int(row.c or 0), int(row.s or 0)


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
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


Emly_Files = EMLYFilesTable(DB)
