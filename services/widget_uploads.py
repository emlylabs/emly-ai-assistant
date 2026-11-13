"""Widget file-upload handling (bot-scoped)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import UploadFile

from config import DATA_DIR
from models.emly_files import Emly_Files
from utils.utils import safe_filename


async def handle_file_uploads(files: List[UploadFile], user_id: str, bot_id: str) -> list[dict]:
    """Persist uploaded files under ``DATA_DIR/attachments/{user_id}/{file_id}/``.

    The ``emly_files`` row carries the bot scope so per-bot admin views
    only show their own attachments.
    """
    uploaded: list[dict] = []
    if not files:
        return uploaded

    for file in files:
        content = await file.read()
        emly_file = Emly_Files.insert_new_file(
            bot_id=bot_id,
            user_id=user_id,
            file_name=file.filename,
            file_type=file.content_type,
            file_size=len(content),
            size_bytes=len(content),
            mime_type=file.content_type,
        )

        user_dir = Path(DATA_DIR) / "attachments" / str(user_id) / str(emly_file.get("id"))
        user_dir.mkdir(parents=True, exist_ok=True)
        file_path = user_dir / safe_filename(file.filename)
        with open(file_path, "wb") as f:
            f.write(content)

        uploaded.append({
            "filename": file.filename,
            "content_type": file.content_type,
            "size": len(content),
            "path": str(file_path),
        })
        await file.seek(0)
    return uploaded
