import ast
import logging
import os
import re
import uuid
from typing import Optional

from fastapi.security import APIKeyHeader

from models.emly_users import EMLYUser, EMLYUsers

log = logging.getLogger("UTILS")


api_key_header = APIKeyHeader(name="X-Emly-UserID", auto_error=False)


def get_emly_user(bot_id: str, emly_user_id: str) -> Optional[EMLYUser]:
    """Retrieve an EMLYUser by ID, scoped to ``bot_id``."""
    return EMLYUsers.get_user_by_id(bot_id, emly_user_id)


_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._\-]+")


def safe_filename(filename: Optional[str]) -> str:
    """Strip path components and unsafe characters from an uploaded filename.

    Prevents path-traversal like ``../../etc/passwd`` reaching the filesystem.
    Falls back to a uuid-based name if nothing usable remains.
    """
    if not filename:
        return f"upload-{uuid.uuid4().hex}"
    name = os.path.basename(filename.replace("\\", "/"))
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip("._")
    return name or f"upload-{uuid.uuid4().hex}"


def get_filtered_citations(citations):
    cits = []
    if citations:
        already_visited_sources = set()
        for citation in citations:
            metadata = citation.get("metadata", {}) or {}
            source = citation.get("source") or metadata.get("source")
            if source not in already_visited_sources:
                already_visited_sources.add(source)
                cits.append({
                    **citation,
                    "og": ast.literal_eval(citation.get("og", "{}")),
                    "payload": ast.literal_eval(citation.get("payload", "{}")),
                })
    return cits


def filter_citations(citations):
    cits = []
    if citations:
        for citation in citations:
            metadata = citation.get("metadata", {}) or {}
            chunk = citation.get("chunk")
            og = metadata.get("og", "{}")
            payload = metadata.get("payload", "{}")
            try:
                og = ast.literal_eval(og) if isinstance(og, str) else og
            except Exception:
                og = {}
            try:
                payload = ast.literal_eval(payload) if isinstance(payload, str) else payload
            except Exception:
                payload = {}
            cits.append({**citation, "chunk": chunk, "og": og, "payload": payload})
    return cits


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
    all_vars = re.findall(r"\{+[^{}]*\}+", text)
    for var in all_vars:
        if var not in allowed_vars:
            text = text.replace(var, "")
    while re.search(r"\{\s*\}", text):
        text = re.sub(r"\{\s*\}+", "", text)
    return text
