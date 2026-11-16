"""Widget file-upload handling (bot-scoped)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import UploadFile

from config import DATA_DIR
from models.emly_files import Emly_Files
from utils.utils import safe_filename

_CHUNK = 1024 * 1024


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
