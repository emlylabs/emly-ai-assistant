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
    created_on: Optional[str] = None
    updated_on: Optional[str] = None


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


def _require_writer(admin_id: str, bot_id: str) -> None:
    m = AdminBotMemberships.get(admin_id, bot_id)
    if m is None or m.role not in ("owner", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this bot")


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
            "created_on": row.get("created_on"),
            "updated_on": row.get("updated_on"),
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
        created_on=row.created_on.isoformat() if row.created_on else None,
        updated_on=row.updated_on.isoformat() if row.updated_on else None,
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
    _require_writer(admin.id, bot.id)
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
    _require_writer(admin.id, bot.id)
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

    return _row_to_response(Emly_Files.get_by_id(bot.id, file_id))


@router.get("/bots/{slug}/files", response_model=List[FileResponse])
def list_files(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    if AdminBotMemberships.get(admin.id, bot.id) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this bot")
    return [_row_to_response(d) for d in Emly_Files.list_for_bot(bot.id)]


@router.delete("/bots/{slug}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    slug: str,
    file_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    if Emly_Files.get_by_id(bot.id, file_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    get_rag_manager().delete_file(bot_id=bot.id, file_id=file_id)
    Emly_Files.delete_by_id(bot.id, file_id)
    file_dir = _bot_uploads_dir(bot.id) / file_id
    if file_dir.exists():
        shutil.rmtree(file_dir, ignore_errors=True)


@router.post("/bots/{slug}/files/{file_id}/reindex", response_model=FileResponse)
def reindex_file(
    slug: str,
    file_id: str,
    background_tasks: BackgroundTasks,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    row = Emly_Files.get_by_id(bot.id, file_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    file_dir = _bot_uploads_dir(bot.id) / file_id
    candidates = list(file_dir.glob("*")) if file_dir.exists() else []
    if not candidates:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Source file missing on disk")
    Emly_Files.update_status(bot.id, file_id, EMBEDDING_STATUS_PENDING)
    _kick_embed(bot.id, file_id, str(candidates[0]), background_tasks)
    return _row_to_response(Emly_Files.get_by_id(bot.id, file_id))


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
    created_on: Optional[str] = None
    updated_on: Optional[str] = None
    completed_on: Optional[str] = None
    error_message: Optional[str] = None


class CrawlJobPageOut(BaseModel):
    id: int
    job_id: str
    url: str
    depth: int
    state: str
    reason: Optional[str] = None
    file_id: Optional[str] = None
    created_on: Optional[str] = None
    updated_on: Optional[str] = None


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
    admin: AdminUserModel = Depends(get_admin),
):
    from services.crawl_fetch import UnsafeUrlError, validate_url
    from models.crawl_jobs import Crawl_Jobs

    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
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
    # Supervisor will pick this up on its next tick.
    return _crawl_job_dict_to_response(job)


@router.get("/bots/{slug}/crawl/jobs", response_model=List[CrawlJobOut])
def list_crawl_jobs(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import Crawl_Jobs

    bot = _resolve_bot(slug)
    if AdminBotMemberships.get(admin.id, bot.id) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this bot")
    return [_crawl_job_dict_to_response(j) for j in Crawl_Jobs.list_for_bot(bot.id, limit=50)]


@router.get("/bots/{slug}/crawl/jobs/{job_id}", response_model=CrawlJobOut)
def get_crawl_job(
    slug: str,
    job_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import Crawl_Jobs

    bot = _resolve_bot(slug)
    if AdminBotMemberships.get(admin.id, bot.id) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this bot")
    job = Crawl_Jobs.get(bot.id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl job not found")
    return _crawl_job_dict_to_response(job)


@router.get(
    "/bots/{slug}/crawl/jobs/{job_id}/pages",
    response_model=List[CrawlJobPageOut],
)
def list_crawl_job_pages(
    slug: str,
    job_id: str,
    limit: int = 200,
    offset: int = 0,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import Crawl_Jobs

    bot = _resolve_bot(slug)
    if AdminBotMemberships.get(admin.id, bot.id) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this bot")
    if Crawl_Jobs.get(bot.id, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl job not found")
    return [CrawlJobPageOut(**p) for p in Crawl_Jobs.list_pages(job_id, limit=limit, offset=offset)]


def _transition_crawl_job(slug: str, job_id: str, admin_id: str, new_status: str) -> CrawlJobOut:
    from models.crawl_jobs import (
        Crawl_Jobs,
        JOB_STATUS_CANCELLED,
        JOB_STATUS_PAUSED,
        JOB_STATUS_RUNNING,
        JOB_TERMINAL_STATUSES,
    )

    bot = _resolve_bot(slug)
    _require_writer(admin_id, bot.id)
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
    return _crawl_job_dict_to_response(Crawl_Jobs.get(bot.id, job_id))


@router.post("/bots/{slug}/crawl/jobs/{job_id}/pause", response_model=CrawlJobOut)
def pause_crawl_job(
    slug: str,
    job_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import JOB_STATUS_PAUSED

    return _transition_crawl_job(slug, job_id, admin.id, JOB_STATUS_PAUSED)


@router.post("/bots/{slug}/crawl/jobs/{job_id}/resume", response_model=CrawlJobOut)
def resume_crawl_job(
    slug: str,
    job_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import JOB_STATUS_RUNNING

    return _transition_crawl_job(slug, job_id, admin.id, JOB_STATUS_RUNNING)


@router.post("/bots/{slug}/crawl/jobs/{job_id}/cancel", response_model=CrawlJobOut)
def cancel_crawl_job(
    slug: str,
    job_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    from models.crawl_jobs import JOB_STATUS_CANCELLED

    return _transition_crawl_job(slug, job_id, admin.id, JOB_STATUS_CANCELLED)


@router.post("/bots/{slug}/reindex", status_code=status.HTTP_202_ACCEPTED)
def reindex_all(
    slug: str,
    background_tasks: BackgroundTasks,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
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
    return {"status": "queued", "files_queued": queued}
