import ast
import json
import traceback
from enum import Enum
from functools import wraps

from fastapi.security import APIKeyHeader
from fastapi import HTTPException, UploadFile, status, Depends

import os
from typing import Annotated, Dict, List

from models.emly_users import EMLYUsers, EMLYUser
from typing import Optional
from utils.constants import ERROR_MESSAGES
import requests
import uuid
import logging
import re
from starlette.responses import JSONResponse
from import_data import import_data_status, vectorize_uploaded_files

log = logging.getLogger("UTILS")


api_key_header = APIKeyHeader(name="X-Emly-UserID", auto_error=False)


def get_emly_user(bot_id: str, emly_user_id: str) -> Optional[EMLYUser]:
    """Retrieve an EMLYUser by ID, scoped to ``bot_id``."""
    return EMLYUsers.get_user_by_id(bot_id, emly_user_id)

def create_emly_user(request, bot_id: str)-> Optional[EMLYUser]:
    """Create a new EMLY user scoped to ``bot_id`` if one does not already exist."""
    emly_user_id = request.headers.get("X-Emly-UserID")
    if emly_user_id is None:
        new_uuid = str(uuid.uuid4())
        emly_user_id = f"emly-gs-{new_uuid}"

    emly_user = get_emly_user(bot_id, emly_user_id)
    if emly_user:
        return emly_user
    meta = {"host": request.client.host, "port": request.client.port}
    return EMLYUsers.insert_new_user(
        bot_id, emly_user_id,
        first_name=None, last_name=None, email=None, phone=None,
        ip=request.headers.get("host"),
        browser=request.headers.get("user-agent"),
        meta=meta,
        country=None, city=None, region=None, latitude=None, longitude=None,
    )

def get_or_create_emly_user(request, bot_id: str, user_id)-> Optional[EMLYUser]:
    emly_user = get_emly_user(bot_id, user_id)
    if emly_user:
        return emly_user
    meta = {"host": request.client.host, "port": request.client.port}
    return EMLYUsers.insert_new_user(
        bot_id, user_id,
        first_name=None, last_name=None, email=None, phone=None,
        ip=request.client.host,
        browser=request.headers.get("user-agent"),
        meta=meta,
        country=None, city=None, region=None, latitude=None, longitude=None,
    )

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._\-]+")


def safe_filename(filename: Optional[str]) -> str:
    """Strip path components and unsafe characters from an uploaded filename.

    Prevents path-traversal attacks like ``../../etc/passwd`` reaching the
    filesystem. Falls back to a uuid-based name if nothing usable remains.
    """
    if not filename:
        return f"upload-{uuid.uuid4().hex}"
    # Strip any directory components (Windows- or POSIX-separated).
    name = os.path.basename(filename.replace("\\", "/"))
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip("._")
    return name or f"upload-{uuid.uuid4().hex}"


class ImportStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NOT_FOUND = "NOT_FOUND"


class ImportData:
    def __init__(self):
        self.requests = {}
        self.current_request_id = None
        self.first_time_import = False

    def set_first_time_import(self, value: bool):
        self.first_time_import = value

    async def create_request_id(self, bot_id: str):
        request_id = str(uuid.uuid4())
        self.requests[request_id] = {
            "status": ImportStatus.PENDING.value,
            "progress": 0,
            "error": None,
            "total_documents": 0,
            "completed_documents": 0,
            "new_documents": 0,
            "deleted_documents": 0,
            "dataset_name": "",
            "last_updated": "",
        }
        self.current_request_id = request_id

        from routes.actions import get_config_json_file, save_config_json_file
        config_data = get_config_json_file(bot_id)
        config_data.pop("last_import_status", None)
        save_config_json_file(config_data, bot_id)

        return request_id

    def create_request_id_normalized(self, bot_id: str):
        request_id = str(uuid.uuid4())
        self.requests[request_id] = {
            "status": ImportStatus.PENDING.value,
            "progress": 0,
            "error": None,
            "total_documents": 0,
            "completed_documents": 0,
            "new_documents": 0,
            "deleted_documents": 0,
            "dataset_name": "",
            "last_updated": "",
        }
        self.current_request_id = request_id

        from routes.actions import get_config_json_file, save_config_json_file
        config_data = get_config_json_file(bot_id)
        config_data.pop("last_import_status", None)
        save_config_json_file(config_data, bot_id)

        return request_id


    async def import_files_data(self, request_id, bot_id: str, files: List[str], method: str = "post"):
        try:
            self.requests[request_id]["status"] = ImportStatus.RUNNING.value
            await vectorize_uploaded_files(bot_id=bot_id, source="folder_bot", files=files, method=method)
            self.requests[request_id]["status"] = ImportStatus.COMPLETED.value
            self.requests[request_id]["progress"] = 100

            from routes.actions import get_config_json_file, save_config_json_file
            config_data = get_config_json_file(bot_id)
            config_data["last_import_identifier"] = bot_id
            save_config_json_file(config_data, bot_id)

        except Exception as e:
            print(f"""Error importing data: {e}""")
            self.requests[request_id]["status"] = ImportStatus.FAILED.value
            self.requests[request_id]["error"] = str(e)

    async def delete_files_data(self, bot_id: str, files: List[str]):
        try:
            await vectorize_uploaded_files(bot_id=bot_id, source="folder_bot", files=files, method="delete")
        except Exception:
            log.exception("Error deleting data for files=%s", files)
            raise

    def get_job_status(self, request_id, bot_id: str):
        if self.current_request_id:
            self.requests[request_id]["total_documents"] = import_data_status.get("total_documents", 0)
            self.requests[request_id]["completed_documents"] = import_data_status.get("completed_documents", 0)
            self.requests[request_id]["new_documents"] = import_data_status.get("documents_to_add", 0)
            self.requests[request_id]["deleted_documents"] = import_data_status.get("documents_to_remove", 0)
            self.requests[request_id]["dataset_name"] = import_data_status.get("dataset_name", "")
            self.requests[request_id]["last_updated"] = import_data_status.get("last_updated", "")

            # calculate progress
            if self.requests[request_id]["total_documents"] and self.requests[request_id]["completed_documents"]:
                total_work = self.requests[request_id]["new_documents"]
                completed_work = self.requests[request_id]["completed_documents"]
                self.requests[request_id]["progress"] = min(
                    100,
                    int((completed_work / total_work * 100) if total_work > 0 else 100)
                )
            else:
                self.requests[request_id]["progress"] = 0

            current_status = self.requests[request_id]["status"]

            if current_status in [ImportStatus.COMPLETED.value, ImportStatus.FAILED.value]:
                job_info = self.requests[request_id].copy()

                # Convert ImportStatus enum to string for JSON serialization
                if isinstance(job_info.get("status"), ImportStatus):
                    job_info["status"] = job_info["status"].value

                if current_status == ImportStatus.COMPLETED.value:
                    job_info["progress"] = 100

                from routes.actions import get_config_json_file, save_config_json_file
                config_data = get_config_json_file(bot_id)
                config_data["last_import_status"] = job_info
                save_config_json_file(config_data, bot_id)

                del self.requests[request_id]
                self.current_request_id = None

                return job_info

            return self.requests[request_id]

        return {"status": ImportStatus.NOT_FOUND.value}

def handle_general_exception(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"message": e.__str__()})
    return wrapper

def get_filtered_citations(citations):
    cits = []
    if citations:
        already_visited_sources = set()
        for citation in citations:
                # Prefer top-level source, fallback to metadata source for deduping
                metadata = citation.get("metadata", {}) or {}
                source = citation.get("source") or metadata.get("source")

                if source not in already_visited_sources:
                    already_visited_sources.add(source)
                    cits.append({
                        **citation,
                        "og": ast.literal_eval(citation.get("og", '{}')),
                        "payload": ast.literal_eval(citation.get("payload", '{}'))
                    })
    return cits

def filter_citations(citations):
    cits = []
    if citations:
        for citation in citations:
            metadata = citation.get("metadata", {}) or {}
            chunk = citation.get("chunk")

            # Parse OG and payload from metadata (if they exist as stringified dicts)
            og = metadata.get("og", '{}')
            payload = metadata.get("payload", '{}')

            try:
                og = ast.literal_eval(og) if isinstance(og, str) else og
            except Exception:
                og = {}

            try:
                payload = ast.literal_eval(payload) if isinstance(payload, str) else payload
            except Exception:
                payload = {}

            cits.append({
                **citation,
                "chunk": chunk,
                "og": og,
                "payload": payload,
            })
    return cits

class ConfigManager:
    """Compatibility shim — wraps ``services.bot_config`` for legacy call
    sites in ``routes/actions.py``.

    Reads/writes go through the bot row in the ``bots`` table — no
    on-disk JSON file. New call sites should import
    ``services.bot_config.get_config_for_bot`` / ``save_config_for_bot``
    directly; this shim exists only so the Phase 4 conversion of routes
    is a small diff instead of a coordinated rewrite.
    """

    def get_config(self, bot_id: str) -> Dict:
        from services.bot_config import get_config_for_bot

        try:
            return get_config_for_bot(bot_id).model_dump(mode="json")
        except LookupError:
            return {}

    def update_config(self, new_config: Dict, bot_id: str) -> bool:
        from services.bot_config import save_config_for_bot

        if not new_config:
            logging.info("Received empty configuration. Keeping existing configuration.")
            return False
        save_config_for_bot(bot_id, new_config)
        return True


config_manager = ConfigManager()

def process_trigger_prompts(data, topic):
    trigger_prompts = ""
    for item in data:
        for key, value in item.items():
            trigger = value.get("trigger", {})
            if trigger.get("value") != "PROMPT":
                continue
            if topic in (trigger.get("belongs_to") or []):
                trigger_prompt = trigger.get("trigger_prompt")
                trigger_code = trigger.get("trigger_code")
                if trigger_prompt and trigger_code:
                    safe_prompt = clean_template_string(trigger_prompt)
                    output = f"{safe_prompt}  respond with  <internal_code>{trigger_code}</internal_code>"
                    trigger_prompts = "\n".join([trigger_prompts, output])
    return trigger_prompts


allowed_vars = {"{context}", "{user_input}", "{filled_slots}", "{history}"}

def clean_template_string(text: str) -> str:
    """Remove unwanted template variables and empty braces."""
    if not text:
        return text
    
    # Find all template variables in the text (including nested braces)
    # This pattern matches single or multiple levels of braces
    all_vars = re.findall(r'\{+[^{}]*\}+', text)
    
    # Remove unwanted variables
    for var in all_vars:
        if var not in allowed_vars:
            text = text.replace(var, '')
    
    # Remove empty braces (single, double, or more)
    # Keep removing until no more empty braces remain
    while re.search(r'\{\s*\}', text):
        text = re.sub(r'\{\s*\}+', '', text)
    
    return text
