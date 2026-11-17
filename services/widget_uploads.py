"""Widget file-upload handling (bot-scoped)."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List

from fastapi import UploadFile

from config import DATA_DIR
from models.emly_files import Emly_Files
from utils.utils import safe_filename

_CHUNK = 1024 * 1024
log = logging.getLogger(__name__)

# S17: the most visitor attachments one bot may hold, in MB. Visitors are
# anonymous, so without a cap they could fill the disk at upload speed.
ATTACHMENT_QUOTA_ENV = "WIDGET_ATTACHMENT_QUOTA_MB"
DEFAULT_ATTACHMENT_QUOTA_MB = 2048


def attachment_quota_bytes() -> int:
    raw = (os.environ.get(ATTACHMENT_QUOTA_ENV) or "").strip()
    try:
        mb = int(raw) if raw else DEFAULT_ATTACHMENT_QUOTA_MB
    except ValueError:
        log.warning("%s=%r is not a whole number of MB; using %d", ATTACHMENT_QUOTA_ENV, raw,
                    DEFAULT_ATTACHMENT_QUOTA_MB)
        mb = DEFAULT_ATTACHMENT_QUOTA_MB
    return max(0, mb) * _CHUNK


def attachment_quota_allows(bot_id: str, incoming_bytes: int) -> bool:
    """Whether the bot's stored visitor attachments plus ``incoming_bytes``
    stay within the quota. Attachments are the file rows with a visitor."""
    from peewee import fn

    from models.emly_files import EMLYFiles

    used = (EMLYFiles.select(fn.COALESCE(fn.SUM(EMLYFiles.size_bytes), 0))
            .where((EMLYFiles.bot == bot_id) & EMLYFiles.user.is_null(False))
            .scalar()) or 0
    return int(used) + int(incoming_bytes) <= attachment_quota_bytes()


async def handle_file_uploads(files: List[UploadFile], user_id: str, bot_id: str) -> list[dict]:
    """Persist uploaded files under ``DATA_DIR/attachments/{user_id}/{file_id}/``.

    The ``emly_files`` row carries the bot scope so per-bot admin views
    only show their own attachments.
    """
    uploaded: list[dict] = []
    if not files:
        return uploaded

    for file in files:
        # The route has checked file.size (bytes Starlette actually
        # received) against the bot's cap. Copy in chunks so a large file
        # is never held in memory whole (G13c).
        size = file.size if file.size is not None else 0
        emly_file = Emly_Files.insert_new_file(
            bot_id=bot_id,
            user_id=user_id,
            file_name=file.filename,
            file_type=file.content_type,
            file_size=size,
            size_bytes=size,
            mime_type=file.content_type,
        )

        user_dir = Path(DATA_DIR) / "attachments" / str(user_id) / str(emly_file.get("id"))
        user_dir.mkdir(parents=True, exist_ok=True)
        file_path = user_dir / safe_filename(file.filename)
        await file.seek(0)
        with open(file_path, "wb") as f:
            while chunk := await file.read(_CHUNK):
                f.write(chunk)

        uploaded.append({
            "filename": file.filename,
            "content_type": file.content_type,
            "size": size,
            "path": str(file_path),
        })
        await file.seek(0)
    return uploaded
