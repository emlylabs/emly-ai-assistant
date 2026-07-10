"""Admin-uploaded file ingestion.

The only ingestion path in the system. Admins upload files via the admin UI;
those files are passed here for chunking, embedding, and upsert into the
shared Qdrant ``bots`` collection.

Phase 5 of the multi-bot plan replaces this module with persistent
``emly_files``-driven re-index orchestration. For Phase 0 it stays a thin
shim around ``services.data_service.DataService.process_file`` that keeps
the existing admin upload route working.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import traceback
from datetime import datetime
from typing import List

from agents.rag_manager import get_rag_manager
from models.emly_docs import EmlyDocs

log = logging.getLogger("ImportData")
import_data_status: dict = {"status": "NOT_STARTED"}


async def import_uploaded_files(bot_id: str, file_paths: List[str]) -> dict:
    """Embed and upsert a batch of admin-uploaded files into Qdrant."""
    from utils.dependencies import DATA_SERVICE_INSTANCE

    if not file_paths:
        raise Exception("No files were uploaded.")

    rag = get_rag_manager()

    import_data_status["total_documents"] = len(file_paths)
    import_data_status["completed_documents"] = 0
    import_data_status["status"] = "RUNNING"

    temp_folder = os.path.join(tempfile.gettempdir(), bot_id)
    os.makedirs(temp_folder, exist_ok=True)

    try:
        emly_docs = EmlyDocs.list_for_bot(bot_id)
        existing = {doc.name: doc for doc in emly_docs}

        new_files: list[tuple[str, str, str, str]] = []
        updated_files: list[tuple[str, str, str, str]] = []

        for file_name in file_paths:
            file_path = os.path.join(temp_folder, file_name)
            with open(file_path, "rb") as f:
                content = f.read()
            file_hash = hashlib.sha256(content).hexdigest()
            name, ext = os.path.splitext(file_name)
            ext = ext.lstrip(".").lower()

            if name not in existing:
                new_files.append((name, ext, file_path, file_hash))
            elif existing[name].content_hash != file_hash:
                updated_files.append((name, ext, file_path, file_hash))

        import_data_status["documents_to_add"] = len(new_files)

        # Updated files: drop the file's existing points and re-embed.
        for name, _ext, file_path, file_hash in updated_files:
            log.info("Updating file: %s", name)
            rag.delete_file(bot_id=bot_id, file_id=name)
            DATA_SERVICE_INSTANCE.process_file(file_path, bot_id=bot_id, file_id=name)
            EmlyDocs.update_emly_doc_by_name(bot_id, name, {"content_hash": file_hash})
            import_data_status["completed_documents"] += 1

        # New files: just embed.
        for name, _ext, file_path, file_hash in new_files:
            log.info("Adding new file: %s", name)
            DATA_SERVICE_INSTANCE.process_file(file_path, bot_id=bot_id, file_id=name)
            EmlyDocs.insert_new_emly_doc(bot_id, name, content_hash=file_hash)
            import_data_status["completed_documents"] += 1

        import_data_status["status"] = "COMPLETED"
        return {
            "status": "SUCCESS",
            "new_files": [f[0] for f in new_files],
            "updated_files": [f[0] for f in updated_files],
        }
    except Exception as e:
        traceback.print_exception(e)
        import_data_status["status"] = "FAILED"
        import_data_status["error"] = str(e)
        raise


async def delete_uploaded_files(bot_id: str, file_names: List[str]) -> dict:
    """Drop a list of files from both Qdrant and the EmlyDocs index."""
    if not file_names:
        raise Exception("No file names provided for deletion.")

    rag = get_rag_manager()

    emly_docs = EmlyDocs.list_for_bot(bot_id)
    existing = {doc.name: doc for doc in emly_docs}

    valid_to_delete = [f for f in file_names if os.path.splitext(f)[0] in existing]
    if not valid_to_delete:
        raise Exception("No matching files found for deletion.")

    for full_name in valid_to_delete:
        name, _ = os.path.splitext(full_name)
        rag.delete_file(bot_id=bot_id, file_id=name)
        EmlyDocs.delete_emly_doc_by_name(bot_id, name)

    return {
        "status": "SUCCESS",
        "deleted_files": valid_to_delete,
        "message": f"Deleted {len(valid_to_delete)} files.",
    }


# ---------------------------------------------------------------------------
# Backwards-compatible aliases consumed by routes/utils until Phase 5 lands.
# ---------------------------------------------------------------------------
async def vectorize_uploaded_files(
    bot_id: str,
    source: str = "folder_bot",
    files: list[str] | None = None,
    method: str = "post",
):
    if files is None:
        files = []
    if method.lower() == "post":
        return await import_uploaded_files(bot_id, files)
    if method.lower() == "delete":
        return await delete_uploaded_files(bot_id, files)
    raise ValueError(f"Unsupported method: {method}")
