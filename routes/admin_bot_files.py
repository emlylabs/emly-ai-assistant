"""Per-bot file upload routes (Phase 5).

Files are persisted to ``{DATA_DIR}/bots/{bot_id}/uploads/{file_id}/<name>``,
their metadata into ``emly_files``, and their chunks into the bot's
slice of the shared Qdrant ``bots`` collection via
``RAGManager.upsert(bot_id, file_id, chunks)``.

Single-replica constraint: ``DATA_DIR`` is a per-pod local volume.
Horizontal scaling requires the S3-backed object storage adapter from
the Future-Items section of the multi-bot plan.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel

from agents.rag_manager import get_rag_manager
from config import DATA_DIR
from models.admin_bot_memberships import AdminBotMemberships
from models.admin_users import AdminUserModel
from models.bots import BotModel, Bots
from models.emly_files import (
    DEFAULT_DOCUMENT_TYPE,
    DOCUMENT_TYPES,
    EMBEDDING_STATUS_EMBEDDED,
    EMBEDDING_STATUS_EMBEDDING,
    EMBEDDING_STATUS_FAILED,
    EMBEDDING_STATUS_PENDING,
    EMLYFiles,
    Emly_Files,
)
from services.auth.dependencies import get_admin
from services.authz import require_member, require_writer
from services.bot_config import get_config_for_bot
from utils.utils import safe_filename

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas & helpers
# ---------------------------------------------------------------------------
class FileResponse(BaseModel):
    id: str
    bot_id: str
    file_name: str
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    embedding_status: str
    error_message: Optional[str] = None
    document_type: str = DEFAULT_DOCUMENT_TYPE
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class FileListResponse(BaseModel):
    """Page of files for a bot plus the aggregates the UI needs to render
    filter chips, the collections rail, and the storage footer without
    second round-trips. ``total`` is the count matching the active
    filters; ``grand_total`` and ``total_bytes`` are unfiltered."""

    items: List[FileResponse]
    total: int
    page: int
    page_size: int
    grand_total: int
    total_bytes: int
    last_updated_at: Optional[str] = None
    status_counts: dict  # {indexed, in_progress, errors} within the collection
    document_type_counts: dict  # {document_type: count}, unfiltered


FILE_PAGE_SIZE_DEFAULT = 50
FILE_PAGE_SIZE_MAX = 200
FILE_STATUS_BUCKETS = ("indexed", "in_progress", "errors")


def _validate_document_type(document_type: str) -> str:
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"document_type must be one of {sorted(DOCUMENT_TYPES)}",
        )
    return document_type


def _resolve_bot(slug: str) -> BotModel:
    bot = Bots.get_by_slug(slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    return bot


def _require_writer(admin: AdminUserModel, bot_id: str) -> None:
    """Honors ``is_superadmin`` via ``services.authz``."""
    require_writer(admin, bot_id)


def _bot_uploads_dir(bot_id: str) -> Path:
    return Path(DATA_DIR) / "bots" / bot_id / "uploads"


def _enforce_upload_limits(bot: BotModel, content_length: Optional[int], mime: Optional[str]) -> None:
    cfg = get_config_for_bot(bot.id)
    limits = cfg.limits

    # Reject archive formats outright (zip-bomb defense).
    blocked_mimes = {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-tar",
        "application/x-7z-compressed",
        "application/x-rar-compressed",
        "application/gzip",
    }
    if mime and mime.lower() in blocked_mimes:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Archive uploads are not allowed (mime={mime})",
        )

    if mime and limits.mime_allowlist and mime.lower() not in {m.lower() for m in limits.mime_allowlist}:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"MIME type {mime} not in this bot's allowlist",
        )

    if content_length is not None and content_length > limits.max_file_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {limits.max_file_size_mb} MB cap for this bot",
        )

    # Per-bot total-storage cap.
    if limits.total_storage_quota_mb is not None:
        used = sum(
            f.size_bytes or 0
            for f in EMLYFiles.select().where(EMLYFiles.bot == bot.id)
        )
        if (used + (content_length or 0)) > limits.total_storage_quota_mb * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="This bot is over its total-storage quota",
            )

    # File-count cap (defense in depth).
    file_count = EMLYFiles.select().where(EMLYFiles.bot == bot.id).count()
    if file_count >= limits.file_count_cap:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This bot has hit the file-count cap ({limits.file_count_cap})",
        )


def _row_to_response(row: dict | EMLYFiles) -> FileResponse:
    if isinstance(row, dict):
        return FileResponse(**{
            "id": row["id"],
            "bot_id": row["bot_id"],
            "file_name": row["file_name"] or "",
            "mime_type": row.get("mime_type"),
            "size_bytes": row.get("size_bytes"),
            "sha256": row.get("sha256"),
            "embedding_status": row["embedding_status"],
            "error_message": row.get("error_message"),
            "document_type": row.get("document_type") or DEFAULT_DOCUMENT_TYPE,
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        })
    return FileResponse(
        id=str(row.id),
        bot_id=row.bot_id,
        file_name=row.file_name or "",
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        embedding_status=row.embedding_status,
        error_message=row.error_message,
        document_type=row.document_type or DEFAULT_DOCUMENT_TYPE,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


# ---------------------------------------------------------------------------
# Embedding pipeline
# ---------------------------------------------------------------------------
def _embed_file_sync(
    bot_id: str,
    file_id: str,
    file_path: str,
    source_url: Optional[str] = None,
) -> None:
    """Run the chunk + embed + upsert flow for a single file.

    Idempotent: drops any existing points for ``(bot_id, file_id)``
    before upserting fresh chunks. The startup-recovery sweep relies on
    this so requeuing a half-done embed never doubles the chunks. The
    ``document_type`` injected into chunk metadata is read from the row
    on disk so reindex / recovery paths automatically pick up edits.
    """
    from utils.dependencies import DATA_SERVICE_INSTANCE

    Emly_Files.update_status(bot_id, file_id, EMBEDDING_STATUS_EMBEDDING)
    try:
        row = Emly_Files.get_by_id(bot_id, file_id)
        doc_type = (row or {}).get("document_type") or DEFAULT_DOCUMENT_TYPE
        # Re-embed must drop any existing points for this file first.
        get_rag_manager().delete_file(bot_id=bot_id, file_id=file_id)
        DATA_SERVICE_INSTANCE.process_file(
            file_path,
            bot_id=bot_id,
            file_id=file_id,
            document_type=doc_type,
            source_url=source_url,
        )
        Emly_Files.update_status(bot_id, file_id, EMBEDDING_STATUS_EMBEDDED)
    except Exception as e:
        log.exception("Embed failed bot=%s file=%s", bot_id, file_id)
        Emly_Files.update_status(bot_id, file_id, EMBEDDING_STATUS_FAILED, error_message=str(e))


def _kick_embed(
    bot_id: str,
    file_id: str,
    file_path: str,
    background_tasks: BackgroundTasks,
    source_url: Optional[str] = None,
) -> None:
    background_tasks.add_task(_embed_file_sync, bot_id, file_id, file_path, source_url)


def recover_pending_embeds() -> int:
    """Startup hook: re-queue files left in pending/embedding state.

    A pod restart mid-embed leaves a row in ``embedding`` forever; this
    sweep finds those (and ``pending``) and either re-runs them or marks
    them failed if the file is no longer on disk.
    """
    pending = EMLYFiles.select().where(
        EMLYFiles.embedding_status.in_([EMBEDDING_STATUS_PENDING, EMBEDDING_STATUS_EMBEDDING])
    )
    requeued = 0
    for row in pending:
        bot_dir = _bot_uploads_dir(row.bot_id) / str(row.id)
        # Find the original file (one file per id-dir).
        candidates = list(bot_dir.glob("*")) if bot_dir.exists() else []
        if not candidates:
            Emly_Files.update_status(
                row.bot_id, str(row.id), EMBEDDING_STATUS_FAILED,
                error_message="upload missing on disk after restart",
            )
            continue
        # Schedule via asyncio so we don't block the boot path.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                asyncio.to_thread(_embed_file_sync, row.bot_id, str(row.id), str(candidates[0]))
            )
        finally:
            loop.close()
        requeued += 1
    return requeued


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/bots/{slug}/files", response_model=FileResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: str = Form(DEFAULT_DOCUMENT_TYPE),
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin, bot.id)
    _validate_document_type(document_type)

    # Reject early at HTTP body limit, before reading the file.
    content_length = request.headers.get("content-length")
    cl_int = int(content_length) if content_length and content_length.isdigit() else None
    _enforce_upload_limits(bot, cl_int, file.content_type)

    file_id = str(uuid.uuid4())
    target_dir = _bot_uploads_dir(bot.id) / file_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_filename(file.filename)

    sha = hashlib.sha256()
    size = 0
    with open(target_path, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            sha.update(chunk)
            size += len(chunk)
            out.write(chunk)

    # Re-validate after writing (Content-Length may have lied).
    cfg = get_config_for_bot(bot.id)
    if size > cfg.limits.max_file_size_mb * 1024 * 1024:
        target_path.unlink(missing_ok=True)
        target_dir.rmdir()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {cfg.limits.max_file_size_mb} MB cap for this bot",
        )

    Emly_Files.insert_new_file(
        id=file_id,
        bot_id=bot.id,
        file_name=file.filename or target_path.name,
        file_type=file.content_type or "application/octet-stream",
        file_size=size,
        size_bytes=size,
        mime_type=file.content_type,
        sha256=sha.hexdigest(),
        document_type=document_type,
    )

    _kick_embed(bot.id, file_id, str(target_path), background_tasks)
    from services.audit import audit

    audit("file.uploaded", admin_id=admin.id, bot_id=bot.id, target_type="file", target_id=file_id,
          payload={"file_name": file.filename or target_path.name, "size_bytes": size,
                   "mime_type": file.content_type, "document_type": document_type},
          request=request)
    return FileResponse(
        id=file_id,
        bot_id=bot.id,
        file_name=file.filename or target_path.name,
        mime_type=file.content_type,
        size_bytes=size,
        sha256=sha.hexdigest(),
        embedding_status=EMBEDDING_STATUS_PENDING,
        document_type=document_type,
    )


class FilePatch(BaseModel):
    document_type: Optional[str] = None


@router.patch("/bots/{slug}/files/{file_id}", response_model=FileResponse)
def patch_file(
    slug: str,
    file_id: str,
    payload: FilePatch,
    background_tasks: BackgroundTasks,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin, bot.id)
    row = Emly_Files.get_by_id(bot.id, file_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if payload.document_type is not None:
        _validate_document_type(payload.document_type)
        Emly_Files.update_document_type(bot.id, file_id, payload.document_type)
        # Reindex so the new document_type lands in chunk metadata. Cheap
        # for small files, and the alternative (stale metadata in Qdrant)
        # is the kind of bug that's invisible until a citation is wrong.
        file_dir = _bot_uploads_dir(bot.id) / file_id
        candidates = list(file_dir.glob("*")) if file_dir.exists() else []
        if candidates:
            Emly_Files.update_status(bot.id, file_id, EMBEDDING_STATUS_PENDING)
            _kick_embed(bot.id, file_id, str(candidates[0]), background_tasks)

    from services.audit import audit

    audit("file.updated", admin_id=admin.id, bot_id=bot.id, target_type="file", target_id=file_id,
          payload=payload.model_dump(exclude_none=True))
    return _row_to_response(Emly_Files.get_by_id(bot.id, file_id))


@router.get("/bots/{slug}/files", response_model=FileListResponse)
def list_files(
    slug: str,
    page: int = 1,
    page_size: int = FILE_PAGE_SIZE_DEFAULT,
    document_types: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    admin: AdminUserModel = Depends(get_admin),
):
    """Return a single page of files for a bot plus aggregates.

    Pagination is server-side so large bots don't ship hundreds of rows
    per request. ``page`` is 1-indexed; ``page_size`` is clamped to
    ``[1, FILE_PAGE_SIZE_MAX]``. ``document_types`` is a
    comma-separated list (e.g. ``"document,support_article"``) so the
    Collections rail can map its UI buckets to multiple raw types;
    ``status`` is one of ``indexed | in_progress | errors``; ``q`` is a
    substring match on filename + mime.
    """
    bot = _resolve_bot(slug)
    require_member(admin, bot.id)
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), FILE_PAGE_SIZE_MAX))
    offset = (page - 1) * page_size

    doc_type_list = (
        [s.strip() for s in document_types.split(",") if s.strip()]
        if document_types
        else None
    )
    status_bucket = status if status in FILE_STATUS_BUCKETS else None

    total = Emly_Files.count_for_bot(
        bot.id, document_types=doc_type_list, status_bucket=status_bucket, q=q
    )
    rows = Emly_Files.list_for_bot(
        bot.id,
        limit=page_size,
        offset=offset,
        document_types=doc_type_list,
        status_bucket=status_bucket,
        q=q,
    )
    grand_total, total_bytes = Emly_Files.storage_summary(bot.id)
    return FileListResponse(
        items=[_row_to_response(d) for d in rows],
        total=total,
        page=page,
        page_size=page_size,
        grand_total=grand_total,
        total_bytes=total_bytes,
        last_updated_at=Emly_Files.last_updated_at(bot.id),
        status_counts=Emly_Files.status_counts(bot.id, document_types=doc_type_list),
        document_type_counts=Emly_Files.document_type_counts(bot.id),
    )


@router.delete("/bots/{slug}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    slug: str,
    file_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin, bot.id)
    if Emly_Files.get_by_id(bot.id, file_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    get_rag_manager().delete_file(bot_id=bot.id, file_id=file_id)
    Emly_Files.delete_by_id(bot.id, file_id)
    file_dir = _bot_uploads_dir(bot.id) / file_id
    if file_dir.exists():
        shutil.rmtree(file_dir, ignore_errors=True)
    from services.audit import audit

    audit("file.deleted", admin_id=admin.id, bot_id=bot.id, target_type="file", target_id=file_id)


@router.post("/bots/{slug}/files/{file_id}/reindex", response_model=FileResponse)
def reindex_file(
    slug: str,
    file_id: str,
    background_tasks: BackgroundTasks,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin, bot.id)
    row = Emly_Files.get_by_id(bot.id, file_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    file_dir = _bot_uploads_dir(bot.id) / file_id
    candidates = list(file_dir.glob("*")) if file_dir.exists() else []
    if not candidates:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Source file missing on disk")
    Emly_Files.update_status(bot.id, file_id, EMBEDDING_STATUS_PENDING)
    _kick_embed(bot.id, file_id, str(candidates[0]), background_tasks)
    from services.audit import audit

    audit("file.reindexed", admin_id=admin.id, bot_id=bot.id, target_type="file", target_id=file_id)
    return _row_to_response(Emly_Files.get_by_id(bot.id, file_id))


# ---------------------------------------------------------------------------
# Cross-bot copy (§11 T11)
# ---------------------------------------------------------------------------
class FileCopyRequest(BaseModel):
    """Body for cross-bot single-file copy. Target slug is in the path."""

    source_slug: str
    file_id: str


@router.post(
    "/bots/{slug}/files/copy-from",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
)
def copy_file_from_bot(
    slug: str,
    payload: FileCopyRequest,
    request: Request,
    admin: AdminUserModel = Depends(get_admin),
):
    """Deep-copy a single file from one bot to another.

    Requires read access on the source and write access on the target.
    Only copies fully-embedded files (otherwise the target would inherit
    a half-built vector slice). The deployment runs one global embedding
    model, so the source's vectors are reused verbatim with the target's
    ``bot_id`` rewritten in the payload — no re-embedding is needed. The
    file on disk is copied so a future re-index on the target still
    works.
    """
    from services.audit import audit

    target = _resolve_bot(slug)
    _require_writer(admin, target.id)
    source = _resolve_bot(payload.source_slug)
    # Read access on the source is enough; the admin's already proven
    # write access on the target via _require_writer above.
    require_member(admin, source.id)
    if source.id == target.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source and target bots are the same",
        )

    source_row = Emly_Files.get_by_id(source.id, payload.file_id)
    if source_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source file not found")
    if source_row.get("embedding_status") != EMBEDDING_STATUS_EMBEDDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Source file is not fully embedded yet — wait for it to finish "
                f"(current status: {source_row.get('embedding_status')})"
            ),
        )

    # 1. Insert the target file row. New id; embedding_status starts as
    #    PENDING and flips to EMBEDDED once the Qdrant clone completes,
    #    so a partial failure surfaces in the Files tab rather than
    #    silently hiding a broken bot.
    new_file_id = f"file-{uuid.uuid4()}"
    Emly_Files.insert_new_file(
        bot_id=target.id,
        file_name=source_row.get("file_name") or "",
        file_type=source_row.get("file_type") or "",
        file_size=int(source_row.get("file_size") or 0),
        size_bytes=source_row.get("size_bytes"),
        mime_type=source_row.get("mime_type"),
        sha256=source_row.get("sha256"),
        id=new_file_id,
        document_type=source_row.get("document_type") or DEFAULT_DOCUMENT_TYPE,
    )

    # 2. Copy the file on disk so future re-index works on the target.
    src_dir = _bot_uploads_dir(source.id) / payload.file_id
    dst_dir = _bot_uploads_dir(target.id) / new_file_id
    if src_dir.exists():
        try:
            shutil.copytree(src_dir, dst_dir)
        except Exception:
            log.exception(
                "File copy failed bot=%s -> bot=%s file=%s", source.id, target.id, new_file_id
            )
            Emly_Files.update_status(
                target.id,
                new_file_id,
                EMBEDDING_STATUS_FAILED,
                error_message="disk copy failed",
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to copy file to target bot's storage",
            )

    # 3. Clone the Qdrant points with the target's bot_id baked in.
    try:
        copied = get_rag_manager().clone_file(
            source_bot_id=source.id,
            source_file_id=payload.file_id,
            target_bot_id=target.id,
            target_file_id=new_file_id,
        )
    except Exception as e:
        log.exception(
            "Point clone failed bot=%s -> bot=%s file=%s", source.id, target.id, new_file_id
        )
        Emly_Files.update_status(
            target.id,
            new_file_id,
            EMBEDDING_STATUS_FAILED,
            error_message=f"vector clone failed: {e}",
        )
        # Leave the on-disk copy in place so the user can retry via a
        # plain re-index — that path re-embeds from disk without
        # touching the source.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clone vectors to target bot",
        )

    Emly_Files.update_status(target.id, new_file_id, EMBEDDING_STATUS_EMBEDDED)
    audit(
        "file.copied",
        admin_id=admin.id,
        bot_id=target.id,
        target_type="file",
        target_id=new_file_id,
        payload={
            "source_bot_id": source.id,
            "source_file_id": payload.file_id,
            "points_copied": copied,
        },
        request=request,
    )
    log.info(
        "file.copy bot=%s file=%s -> bot=%s file=%s points=%d by=%s",
        source.id,
        payload.file_id,
        target.id,
        new_file_id,
        copied,
        admin.id,
    )
    return _row_to_response(Emly_Files.get_by_id(target.id, new_file_id))


# ---------------------------------------------------------------------------
# Crawl jobs — backend-resident, recoverable. Replaces the previous
# in-browser crawler whose state was lost on tab refresh.
# ---------------------------------------------------------------------------
class CrawlJobOptions(BaseModel):
    sameHostOnly: bool = True
    pathPrefix: str = ""
    includeRegex: str = ""
    excludeRegex: str = ""
    maxDepth: int = 3
    maxPages: int = 100
    respectRobots: bool = True
    skipThinPages: bool = True
    thinThreshold: int = 200
    skipAlreadyImported: bool = True
    politeDelayMs: int = 250
    concurrency: int = 3


class CrawlJobCreate(BaseModel):
    seed_url: str
    document_type: str = "web_page"
    options: CrawlJobOptions = CrawlJobOptions()


class CrawlJobOut(BaseModel):
    id: str
    bot_id: str
    seed_url: str
    options: dict
    status: str
    pages_total: int
    pages_done: int
    pages_skipped: int
    pages_failed: int
    document_type: str
    created_by_admin_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


class CrawlJobPageOut(BaseModel):
    id: int
    job_id: str
    url: str
    depth: int
    state: str
    reason: Optional[str] = None
    file_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _crawl_job_dict_to_response(d: dict) -> CrawlJobOut:
    return CrawlJobOut(**d)


@router.post(
    "/bots/{slug}/crawl/jobs",
    response_model=CrawlJobOut,
    status_code=status.HTTP_201_CREATED,
)
def create_crawl_job(
    slug: str,
    payload: CrawlJobCreate,
    request: Request,
    admin: AdminUserModel = Depends(get_admin),
):
    from services.audit import audit
    from services.crawl_fetch import UnsafeUrlError, validate_url
    from models.crawl_jobs import Crawl_Jobs

    bot = _resolve_bot(slug)
    _require_writer(admin, bot.id)
    _validate_document_type(payload.document_type)

    try:
        validate_url(payload.seed_url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job = Crawl_Jobs.create(
        bot_id=bot.id,
        seed_url=payload.seed_url,
        options=payload.options.model_dump(),
        document_type=payload.document_type,
        created_by_admin_id=admin.id,
    )
    audit(
        "crawl.created",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="crawl_job",
        target_id=job["id"],
        payload={"seed_url": payload.seed_url, "document_type": payload.document_type},
        request=request,
    )
    # Supervisor will pick this up on its next tick.
    return _crawl_job_dict_to_response(job)


@router.get("/bots/{slug}/crawl/jobs", response_model=List[CrawlJobOut])
def list_crawl_jobs(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import Crawl_Jobs

    bot = _resolve_bot(slug)
    require_member(admin, bot.id)
    return [_crawl_job_dict_to_response(j) for j in Crawl_Jobs.list_for_bot(bot.id, limit=50)]


@router.get("/bots/{slug}/crawl/jobs/{job_id}", response_model=CrawlJobOut)
def get_crawl_job(
    slug: str,
    job_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import Crawl_Jobs

    bot = _resolve_bot(slug)
    require_member(admin, bot.id)
    job = Crawl_Jobs.get(bot.id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl job not found")
    return _crawl_job_dict_to_response(job)


class CrawlJobPagesResponse(BaseModel):
    """Page slice + the per-state counts so the UI can render a paginator
    and filter chips without a second round-trip. ``total`` is the count
    matching the active ``states`` filter (or all pages when none)."""

    items: List[CrawlJobPageOut]
    total: int
    counts: dict[str, int]
    limit: int
    offset: int


@router.get(
    "/bots/{slug}/crawl/jobs/{job_id}/pages",
    response_model=CrawlJobPagesResponse,
)
def list_crawl_job_pages(
    slug: str,
    job_id: str,
    limit: int = 100,
    offset: int = 0,
    states: Optional[str] = None,
    admin: AdminUserModel = Depends(get_admin),
):
    """Paginated, optionally state-filtered page slice for a crawl job.

    ``states`` is a comma-separated list (e.g. ``"failed,skipped"``). Big
    crawls can produce thousands of pages; the prior implementation
    returned everything up to 500 and rendered them all at once, which
    silently truncated past 500 and DOM-stalled the browser. ``counts``
    is returned unfiltered so the filter chips can show totals.
    """
    from models.crawl_jobs import Crawl_Jobs

    bot = _resolve_bot(slug)
    require_member(admin, bot.id)
    if Crawl_Jobs.get(bot.id, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl job not found")
    # Clamp pagination so a malformed (or hostile) request can't fetch
    # everything in one shot.
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    state_filter = [s.strip() for s in states.split(",") if s.strip()] if states else None
    items = Crawl_Jobs.list_pages(
        job_id, limit=limit, offset=offset, states=state_filter
    )
    # Counts are unfiltered so the chips show "Failed (12)" regardless of
    # which filter is currently active.
    counts = {
        s: Crawl_Jobs.count_pages(job_id, state=s)
        for s in ("queued", "fetching", "uploading", "embedding", "done", "skipped", "failed")
    }
    if state_filter:
        total = sum(counts.get(s, 0) for s in state_filter)
    else:
        total = sum(counts.values())
    return CrawlJobPagesResponse(
        items=[CrawlJobPageOut(**p) for p in items],
        total=total,
        counts=counts,
        limit=limit,
        offset=offset,
    )


# Audit-event names for the four crawl lifecycle transitions. Keyed by the
# new_status the route is moving the job to.
_CRAWL_AUDIT_ACTIONS = {
    "paused": "crawl.paused",
    "running": "crawl.resumed",
    "cancelled": "crawl.cancelled",
}


def _transition_crawl_job(
    slug: str,
    job_id: str,
    admin_id: str,
    new_status: str,
    request: Optional[Request] = None,
) -> CrawlJobOut:
    from services.audit import audit
    from models.crawl_jobs import (
        Crawl_Jobs,
        JOB_STATUS_CANCELLED,
        JOB_STATUS_PAUSED,
        JOB_STATUS_RUNNING,
        JOB_TERMINAL_STATUSES,
    )

    bot = _resolve_bot(slug)
    _require_writer(admin, bot.id)
    job = Crawl_Jobs.get(bot.id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl job not found")
    if job["status"] in JOB_TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is already {job['status']}",
        )
    completed = new_status == JOB_STATUS_CANCELLED
    Crawl_Jobs.update_status(job_id, new_status, completed=completed)
    action = _CRAWL_AUDIT_ACTIONS.get(new_status)
    if action is not None:
        audit(
            action,
            admin_id=admin_id,
            bot_id=bot.id,
            target_type="crawl_job",
            target_id=job_id,
            payload={"from_status": job["status"], "to_status": new_status},
            request=request,
        )
    return _crawl_job_dict_to_response(Crawl_Jobs.get(bot.id, job_id))


@router.post("/bots/{slug}/crawl/jobs/{job_id}/pause", response_model=CrawlJobOut)
def pause_crawl_job(
    slug: str,
    job_id: str,
    request: Request,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import JOB_STATUS_PAUSED

    return _transition_crawl_job(slug, job_id, admin.id, JOB_STATUS_PAUSED, request=request)


@router.post("/bots/{slug}/crawl/jobs/{job_id}/resume", response_model=CrawlJobOut)
def resume_crawl_job(
    slug: str,
    job_id: str,
    request: Request,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import JOB_STATUS_RUNNING

    return _transition_crawl_job(slug, job_id, admin.id, JOB_STATUS_RUNNING, request=request)


@router.post("/bots/{slug}/crawl/jobs/{job_id}/cancel", response_model=CrawlJobOut)
def cancel_crawl_job(
    slug: str,
    job_id: str,
    request: Request,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import JOB_STATUS_CANCELLED

    return _transition_crawl_job(slug, job_id, admin.id, JOB_STATUS_CANCELLED, request=request)


# ---------------------------------------------------------------------------
# Recurring crawl schedules (§11 T5)
# ---------------------------------------------------------------------------
class CrawlScheduleCreate(BaseModel):
    seed_url: str
    document_type: str = "web_page"
    options: CrawlJobOptions = CrawlJobOptions()
    cadence: str
    run_immediately: bool = True


class CrawlScheduleUpdate(BaseModel):
    """Partial update — only ``enabled`` is mutable today. Cadence
    changes would also need to recompute ``next_run_at`` so they're
    deferred until there's demand; admins can delete + recreate."""

    enabled: bool


class CrawlScheduleOut(BaseModel):
    id: str
    bot_id: str
    seed_url: str
    options: dict
    document_type: str
    cadence: str
    enabled: bool
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_job_id: Optional[str] = None
    created_by_admin_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@router.post(
    "/bots/{slug}/crawl/schedules",
    response_model=CrawlScheduleOut,
    status_code=status.HTTP_201_CREATED,
)
def create_crawl_schedule(
    slug: str,
    payload: CrawlScheduleCreate,
    request: Request,
    admin: AdminUserModel = Depends(get_admin),
):
    from services.audit import audit
    from services.crawl_fetch import UnsafeUrlError, validate_url
    from models.crawl_schedules import Crawl_Schedules, parse_cadence

    bot = _resolve_bot(slug)
    _require_writer(admin, bot.id)
    _validate_document_type(payload.document_type)

    try:
        validate_url(payload.seed_url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        parse_cadence(payload.cadence)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    sched = Crawl_Schedules.create(
        bot_id=bot.id,
        seed_url=payload.seed_url,
        options=payload.options.model_dump(),
        document_type=payload.document_type,
        cadence=payload.cadence,
        created_by_admin_id=admin.id,
        run_immediately=payload.run_immediately,
    )
    audit(
        "crawl.schedule.created",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="crawl_schedule",
        target_id=sched["id"],
        payload={
            "seed_url": payload.seed_url,
            "cadence": payload.cadence,
            "run_immediately": payload.run_immediately,
        },
        request=request,
    )
    return CrawlScheduleOut(**sched)


@router.get("/bots/{slug}/crawl/schedules", response_model=List[CrawlScheduleOut])
def list_crawl_schedules(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_schedules import Crawl_Schedules

    bot = _resolve_bot(slug)
    require_member(admin, bot.id)
    return [CrawlScheduleOut(**s) for s in Crawl_Schedules.list_for_bot(bot.id)]


@router.patch(
    "/bots/{slug}/crawl/schedules/{schedule_id}",
    response_model=CrawlScheduleOut,
)
def patch_crawl_schedule(
    slug: str,
    schedule_id: str,
    payload: CrawlScheduleUpdate,
    request: Request,
    admin: AdminUserModel = Depends(get_admin),
):
    from services.audit import audit
    from models.crawl_schedules import Crawl_Schedules

    bot = _resolve_bot(slug)
    _require_writer(admin, bot.id)
    existing = Crawl_Schedules.get(bot.id, schedule_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    Crawl_Schedules.set_enabled(bot.id, schedule_id, payload.enabled)
    audit(
        "crawl.schedule.updated",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="crawl_schedule",
        target_id=schedule_id,
        payload={"enabled": payload.enabled},
        request=request,
    )
    return CrawlScheduleOut(**Crawl_Schedules.get(bot.id, schedule_id))


@router.delete(
    "/bots/{slug}/crawl/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_crawl_schedule(
    slug: str,
    schedule_id: str,
    request: Request,
    admin: AdminUserModel = Depends(get_admin),
):
    from services.audit import audit
    from models.crawl_schedules import Crawl_Schedules

    bot = _resolve_bot(slug)
    _require_writer(admin, bot.id)
    if Crawl_Schedules.get(bot.id, schedule_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    Crawl_Schedules.delete(bot.id, schedule_id)
    audit(
        "crawl.schedule.deleted",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="crawl_schedule",
        target_id=schedule_id,
        request=request,
    )


@router.post("/bots/{slug}/reindex", status_code=status.HTTP_202_ACCEPTED)
def reindex_all(
    slug: str,
    background_tasks: BackgroundTasks,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin, bot.id)
    # Drop the bot's entire vector slice and re-embed every file.
    get_rag_manager().delete_bot(bot.id)
    rows = Emly_Files.list_for_bot(bot.id)
    queued = 0
    for row in rows:
        file_dir = _bot_uploads_dir(bot.id) / row["id"]
        candidates = list(file_dir.glob("*")) if file_dir.exists() else []
        if not candidates:
            Emly_Files.update_status(
                bot.id, row["id"], EMBEDDING_STATUS_FAILED,
                error_message="upload missing on disk during reindex",
            )
            continue
        Emly_Files.update_status(bot.id, row["id"], EMBEDDING_STATUS_PENDING)
        _kick_embed(bot.id, row["id"], str(candidates[0]), background_tasks)
        queued += 1
    from services.audit import audit

    audit("files.reindexed_all", admin_id=admin.id, bot_id=bot.id, target_type="bot", target_id=bot.id,
          payload={"files_queued": queued})
    return {"status": "queued", "files_queued": queued}
