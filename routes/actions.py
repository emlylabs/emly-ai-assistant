import base64
import time
import traceback
from datetime import datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import lru_cache
from pathlib import Path

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import (
    APIRouter,
    Request,
    HTTPException,
    status,
    UploadFile,
    File,
    Form,
)
from starlette.background import BackgroundTask
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse

from pydantic import BaseModel

import os
import random
import re
import uuid
import aiohttp
import logging


from typing import Optional, List, Union, Dict


from config import (
    SRC_LOG_LEVELS,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    MAX_TOKENS,
    MODEL,
    NUMBER_OF_LAST_MESSAGES,
    EMAIL_SERVER, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD, EMAIL_FROM,
    EMAIL_DELAY_IN_MINUTES, PROCESS_POOL_SIZE, MAX_JOB_INSTANCES,
    LATEST_N_MESSAGES, TEMPERATURE, EMAIL_OTP_TEMPLATE,
    TIMEZONE)

from models.emly_messages import EMLYMessages
from models.emly_users import EMLYUserUpdateForm, EMLYUsers
from models.bot_impressions import Bot_Impressions
from models.bots import Bots
from fastapi import Query
from services.email_service import EmailSender
import mistune

from models.emly_user_action import USER_ACTIONS, EMLYUserActions

from models.emly_files import Emly_Files
from config import DATA_DIR

from apscheduler.executors.pool import ThreadPoolExecutor

from utils.agents import OpenAIAgent

from models.otp_auth import OtpAuthorization

from utils.utils import config_manager
from utils.utils import safe_filename
import requests


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["ACTIONS"])


class EMLYUserResponse(BaseModel):
    id: str
    first_name: Optional[str]
    last_name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    ip: str
    browser: str
    timestamp: int
    created_on: Optional[datetime]
    updated_on: Optional[datetime]
    meta: Optional[dict]


class EMLYResponse(BaseModel):
    user: EMLYUserResponse
    message: Optional[str] = None
    status: Optional[str] = None
    status_code: Optional[int] = None


router = APIRouter()


def _bot_id_from_request(request: Request) -> str:
    """Resolve ``bot_id`` from the ``X-Emly-BotID`` header injected by ``CustomMiddleware``.

    All ``/emly/api/*`` routes are tenant-scoped, so a missing header is a
    client error — there is no implicit "default bot" anymore.
    """
    bot_id = getattr(request.state, "bot_id", None) or request.headers.get("X-Emly-BotID")
    if not bot_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bot ID is required (X-Emly-BotID header)")
    return bot_id



# Initialize job stores, executors, and defaults
jobstores = {
    'default': SQLAlchemyJobStore(url=f'sqlite:///{DATA_DIR}/actions.db')
}
executors = {
    'default': ThreadPoolExecutor(PROCESS_POOL_SIZE)
}

job_defaults = {
    'coalesce': False,
    'max_instances': MAX_JOB_INSTANCES
}

# Create the scheduler
scheduler = BackgroundScheduler(jobstores=jobstores, executors=executors, job_defaults=job_defaults, timezone='Asia/Kolkata')
scheduler.start()


async def update_useful_message(bot_id: str, message_id: str, not_useful: bool = False):
    """Mark a message useful/not-useful within ``bot_id``."""
    try:
        message = EMLYMessages.get_message_by_id(bot_id, message_id)
        if not message:
            return JSONResponse(
                status_code=404,
                content={"message": f"Message having id '{message_id}' not found"},
            )

        updated_message = EMLYMessages.update_emly_message_by_id(bot_id, message_id, not_useful=not_useful)
        if updated_message:
            return {"message": "Message considered as useful"}
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Exception occurred while updating the message"},
        )
    except Exception as e:
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": f"Exception occurred while updating the message: {str(e)}"},
        )


@router.put("/api/message/{message_id}")
async def update_useful_message_route(
    message_id: str,
    request: Request,
    not_useful: bool = Query(default=False),
):
    return await update_useful_message(_bot_id_from_request(request), message_id, not_useful)


def _insert_user_race_safe(*, bot_id, user_id, form_data, ip, browser):
    """Insert an end-user, tolerating a concurrent first-request race.

    The PK is composite ``(bot_id, id)`` (see migration 011), so the only
    integrity error we can hit here is two concurrent first-time requests
    for the same ``(bot_id, user_id)``. Re-read scoped to the bot; if the
    row now exists, return it; otherwise re-raise the original error.
    """
    try:
        return EMLYUsers.insert_new_user(
            bot_id, user_id, form_data.first_name, form_data.last_name,
            form_data.email, form_data.phone, ip, browser, form_data.meta,
            form_data.country, form_data.city, form_data.region,
            form_data.longitude, form_data.latitude,
        )
    except Exception:
        existing = EMLYUsers.get_user_by_id(bot_id, user_id)
        if existing is not None:
            log.info(
                "Concurrent insert for bot=%s user=%s — using existing row",
                bot_id, user_id,
            )
            return existing
        raise


@router.put("/api/user", response_model=EMLYResponse)
async def update_user(
        form_data: EMLYUserUpdateForm,
        request: Request,
        message_id: str = Query(None),
        customer_email: str = Query(None),
        form_title: str = Query(None)
):
    """
    Update current user information.

    Args:
        form_data (EMLYUserUpdateForm): The form data containing updated emly user information.

    Returns:
        dict: A success message with the status if the update is successful.

    Raises:
        HTTPException: If the user is not found, or an error occurs during the update process.
    """
    try:
        bot_id = _bot_id_from_request(request)
        user_id = request.headers.get("X-Emly-UserID")
        session_id = request.headers.get("X-Emly-SessionID")
        page = request.headers.get("X-Emly-PageID")

        emly_user_id = user_id or f"emly-gs-{uuid.uuid4()}"

        emly_user = EMLYUsers.get_user_by_id(bot_id, user_id) if user_id else None

        if not emly_user:
            ip = request.client.host
            browser = request.headers.get("user-agent")
            form_data.meta = {"host": request.client.host, "port": request.client.port}
            emly_user = _insert_user_race_safe(
                bot_id=bot_id,
                user_id=emly_user_id,
                form_data=form_data,
                ip=ip,
                browser=browser,
            )
            user_id = emly_user.id

        if emly_user:
            emly_user = EMLYUsers.update_user(bot_id, user_id, form_data)
            if emly_user:
                response = EMLYResponse(user=EMLYUserResponse(**emly_user.model_dump()),
                                        message="User updated successfully")

                if customer_email:
                    message_count = len(EMLYMessages.get_messages(
                        bot_id=bot_id,
                        user_id=user_id,
                        session_id=session_id,
                        skip=0,
                        limit=3 * 2
                    ))

                    if message_count == 0:
                        scheduler.add_job(
                            send_scheduled_email,
                            run_date=datetime.now(TIMEZONE) + timedelta(minutes=EMAIL_DELAY_IN_MINUTES),
                            args=[
                                customer_email,
                                user_id,
                                {"session_id": session_id},
                                form_title,
                                [],
                                page,
                                "",
                                bot_id,
                            ],
                        )
                    else:
                        scheduler.add_job(
                            send_scheduled_email,
                            args=[
                                customer_email,
                                user_id,
                                {"session_id": session_id},
                                form_title,
                                [],
                                page,
                                "",
                                bot_id,
                            ],
                        )

                    await create_event(bot_id, user_id, session_id, message_id, "callback", "submitted",
                                       action_payload=form_data.__dict__)

                return response
            else:
                # Return an error if the update fails
                return JSONResponse(status_code=500,
                                    content={"message": "Exception occurred while updating the user"})
        else:
            # Return an error if the user is not found
            return JSONResponse(status_code=status.HTTP_404_NOT_FOUND,
                                content={"message": f"User not found with id {user_id}"})

    except Exception as e:
        traceback.print_exception(e)
        # Raise an HTTPException with the full traceback information if an error occurs
        raise HTTPException(status_code=500,
                            detail={"message": f"Exception occurred: {e.__str__()}", "status": "error"})


@router.get("/api/user", response_model=EMLYResponse)
async def get_emly_user(request: Request):
    bot_id = _bot_id_from_request(request)
    user_id = request.headers.get("X-Emly-UserID")
    emly_user = EMLYUsers.get_user_by_id(bot_id, user_id)
    if emly_user:
        return EMLYResponse(user=EMLYUserResponse(**emly_user.model_dump()), message="Success", status="success",
                            status_code=200)
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"message": f"User not found having id '{user_id}'", "status": "failed", "status_code": 404},
    )


def save_config_json_file(payload: dict, bot_id: str):
    """Persist the bot's config row."""
    from services.bot_config import save_config_for_bot

    try:
        save_config_for_bot(bot_id, payload)
        return {"message": "Configuration saved successfully"}
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        log.exception("Failed to save configuration")
        raise HTTPException(status_code=500, detail="Failed to save configuration")


@router.get("/api/config")
def get_config_file(request: Request):
    return get_config_json_file(_bot_id_from_request(request))


def get_config_json_file(bot_id: str) -> dict:
    """Return the bot's config_json as a plain dict (for legacy callers)."""
    from services.bot_config import get_config_for_bot

    try:
        return get_config_for_bot(bot_id).model_dump(mode="json")
    except LookupError:
        return {}


def clean_key(key):
    return ' '.join(word.capitalize() for word in key.replace('_', ' ').split())


# Inline CSS only — Gmail/Outlook strip <style> blocks. Table-based layout
# for the same reason: it's what email clients actually render reliably.
_EMAIL_BRAND_COLOR = "#0F62FE"
_EMAIL_BRAND_DARK = "#0043CE"
_EMAIL_TEXT = "#1F2937"
_EMAIL_MUTED = "#6B7280"
_EMAIL_BORDER = "#E5E7EB"
_EMAIL_BG = "#F3F4F6"
_EMAIL_CARD_BG = "#FFFFFF"
_EMAIL_BOT_BG = "#F9FAFB"
_EMAIL_USER_BG = "#EFF6FF"


def _html_escape(value) -> str:
    import html as _html
    return _html.escape("" if value is None else str(value))


def _normalize_summary_to_html(raw: str) -> str:
    # SUMMARY_RECOMMENDATION_PROMPT asks the LLM for HTML. Running already-rendered
    # HTML through a markdown parser eats the block tags, so only invoke mistune
    # when the model fell back to markdown. Strip stray ```html ... ``` fences first.
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text).strip()
    if text.startswith("<"):
        return text
    return mistune.create_markdown()(text)


def _render_user_details_rows(data: dict) -> tuple[str, str]:
    """Return (main_rows_html, meta_rows_html) from a user-dict payload."""
    rows = []
    meta_rows = []
    for key, value in data.items():
        if key == "actionLimits" or value in (None, "", {}):
            continue
        if isinstance(value, dict):
            for mk, mv in value.items():
                if mv in (None, ""):
                    continue
                meta_rows.append((clean_key(mk), mv))
        else:
            rows.append((clean_key(key), value))

    def _row(k, v):
        return (
            '<tr>'
            f'<td style="padding:8px 12px;border-bottom:1px solid {_EMAIL_BORDER};'
            f'color:{_EMAIL_MUTED};font-size:13px;width:40%;">{_html_escape(k)}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid {_EMAIL_BORDER};'
            f'color:{_EMAIL_TEXT};font-size:14px;">{_html_escape(v)}</td>'
            '</tr>'
        )

    return ("".join(_row(k, v) for k, v in rows),
            "".join(_row(k, v) for k, v in meta_rows))


def _render_transcript(messages) -> str:
    """Render an ordered list of (role, message) into chat bubbles.

    ``messages`` is the Peewee message list as returned by
    ``EMLYMessages.get_messages_v2`` (newest first). We display oldest
    first so the email reads naturally.
    """
    if not messages:
        return (
            f'<p style="margin:0;color:{_EMAIL_MUTED};font-size:14px;">'
            'No messages were exchanged in this session.'
            '</p>'
        )

    md = mistune.create_markdown()
    bubbles = []
    for m in reversed(messages):
        role = (getattr(m, "role", "") or "").lower()
        is_user = role == "user"
        body = getattr(m, "message", "") or ""
        rendered = _html_escape(body) if is_user else md(body)
        rendered = rendered.replace("\n", "<br>") if is_user else rendered
        label = "User" if is_user else "Assistant"
        bg = _EMAIL_USER_BG if is_user else _EMAIL_BOT_BG
        align = "right" if is_user else "left"
        bubbles.append(
            f'<tr><td align="{align}" style="padding:6px 0;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="max-width:480px;display:inline-block;">'
            f'<tr><td style="padding:0 0 4px 0;color:{_EMAIL_MUTED};'
            f'font-size:11px;text-transform:uppercase;letter-spacing:.04em;'
            f'text-align:{align};">{label}</td></tr>'
            f'<tr><td style="background:{bg};border:1px solid {_EMAIL_BORDER};'
            f'border-radius:10px;padding:10px 14px;color:{_EMAIL_TEXT};'
            f'font-size:14px;line-height:1.5;text-align:left;">{rendered}</td></tr>'
            f'</table></td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        + "".join(bubbles)
        + '</table>'
    )


def _build_conversation_url(bot_id: str, session_id: str) -> str:
    """Deep link to the admin conversation thread for view/update/resolve."""
    base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
    slug = None
    if bot_id:
        try:
            bot = Bots.get_by_id(bot_id)
            slug = bot.slug if bot else None
        except Exception:
            log.exception("Failed to resolve bot slug for bot_id=%s", bot_id)
    if not slug or not session_id:
        return ""
    from urllib.parse import quote
    return f"{base}/bots/{quote(slug)}/conversations?session_id={quote(session_id)}"


def _render_email_html(
    *,
    user_details: dict,
    messages,
    summary_html: str,
    conversation_url: str,
    form_title: str,
    request_date: str,
) -> str:
    user_rows, meta_rows = _render_user_details_rows(user_details)
    title = _html_escape(form_title) if form_title else "Conversation summary"

    cta_block = ""
    if conversation_url:
        cta_block = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:24px 0;"><tr><td>'
            f'<a href="{conversation_url}" '
            f'style="background:{_EMAIL_BRAND_COLOR};color:#FFFFFF;'
            f'text-decoration:none;padding:12px 22px;border-radius:8px;'
            f'font-size:14px;font-weight:600;display:inline-block;">'
            f'View, update &amp; resolve conversation'
            f'</a>'
            f'</td></tr><tr><td style="padding-top:8px;color:{_EMAIL_MUTED};'
            f'font-size:12px;">Opens the admin console where you can review the '
            f'full thread, edit details, and mark it resolved.</td></tr></table>'
        )

    summary_section = ""
    if summary_html:
        summary_section = (
            f'<h2 style="margin:0 0 12px 0;color:{_EMAIL_TEXT};font-size:16px;'
            f'font-weight:600;">Summary</h2>'
            f'<div style="color:{_EMAIL_TEXT};font-size:14px;line-height:1.6;">'
            f'{summary_html}</div>'
        )

    transcript_section = (
        f'<h2 style="margin:24px 0 12px 0;color:{_EMAIL_TEXT};font-size:16px;'
        f'font-weight:600;">Conversation transcript</h2>'
        + _render_transcript(messages)
    )

    user_section = ""
    if user_rows or meta_rows:
        user_section = (
            f'<h2 style="margin:24px 0 12px 0;color:{_EMAIL_TEXT};font-size:16px;'
            f'font-weight:600;">User details</h2>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'border="0" style="border:1px solid {_EMAIL_BORDER};border-radius:8px;'
            f'border-collapse:separate;overflow:hidden;">'
            f'{user_rows}'
            + (
                f'<tr><td colspan="2" style="padding:10px 12px;background:{_EMAIL_BG};'
                f'color:{_EMAIL_MUTED};font-size:12px;text-transform:uppercase;'
                f'letter-spacing:.04em;border-bottom:1px solid {_EMAIL_BORDER};">'
                f'Additional metadata</td></tr>{meta_rows}'
                if meta_rows
                else ""
            )
            + '</table>'
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body style="margin:0;padding:0;background:{_EMAIL_BG};
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
  style="background:{_EMAIL_BG};padding:24px 0;">
  <tr><td align="center">
    <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0"
      style="max-width:640px;width:100%;background:{_EMAIL_CARD_BG};
      border:1px solid {_EMAIL_BORDER};border-radius:12px;overflow:hidden;">
      <tr><td style="background:linear-gradient(135deg,{_EMAIL_BRAND_COLOR},{_EMAIL_BRAND_DARK});
        padding:20px 28px;color:#FFFFFF;">
        <div style="font-size:12px;text-transform:uppercase;letter-spacing:.08em;
          opacity:.85;">Emly Labs</div>
        <div style="font-size:20px;font-weight:600;margin-top:4px;">{title}</div>
        <div style="font-size:12px;opacity:.85;margin-top:6px;">{_html_escape(request_date)}</div>
      </td></tr>
      <tr><td style="padding:24px 28px;">
        {summary_section}
        {cta_block}
        {transcript_section}
        {user_section}
      </td></tr>
      <tr><td style="padding:16px 28px;background:{_EMAIL_BG};color:{_EMAIL_MUTED};
        font-size:12px;text-align:center;border-top:1px solid {_EMAIL_BORDER};">
        This is an automated message from Emly Labs. Please do not reply to this email.
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


async def create_event(bot_id: str, user_id: str, session_id: str, message_id: str, action_name: str,
                       action_value: str, action_payload: dict) -> EMLYUserActions:
    """Creates a user action scoped to ``bot_id``."""
    log.info("Starting create_event with bot_id=%s user_id=%s, session_id=%s, message_id=%s, action_name=%s",
             bot_id, user_id, session_id, message_id, action_name)

    if message_id:
        message = EMLYMessages.get_message_by_id(bot_id, message_id)
        if not message:
            log.error("Message with ID '%s' not found", message_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message with ID '{message_id}' not found",
            )

    fields_to_create = {
        'bot_id': bot_id,
        'user_id': user_id,
        'session_id': session_id,
        'message_id': message_id,
        "action_name": action_name,
        "action_value": action_value,
        "action_payload": action_payload,
    }
    user_action = USER_ACTIONS.insert_new_action(**fields_to_create)
    log.info("User action created successfully with ID: %s", user_action["id"])
    return user_action


def create_otp(payload_data, bot_id: str):
    """Issue (or refresh) an OTP for a user under ``bot_id``."""
    expires_in = config_manager.get_config(bot_id).get("otp", {}).get("expires_in", 5)
    otp = OtpAuthorization.get_otp(bot_id=bot_id, user_id=payload_data["user_id"])
    if otp:
        otp = OtpAuthorization.update_otp(bot_id=bot_id, user_id=payload_data["user_id"], otp=generate_otp(),
                                          expires_in=expires_in)
    else:
        otp = OtpAuthorization.insert_otp(
            bot_id=bot_id,
            user_id=payload_data["user_id"],
            otp_type=payload_data["otp_type"],
            otp=generate_otp(),
            expires_in=expires_in,
            authorized=False)
    payload_data["otp"] = otp["otp"]
    # If messages exist, send immediately
    scheduler.add_job(
        send_otp_in_email,
        args=[
            payload_data
        ]
    )
    return otp


def authorize_otp(payload_data, bot_id: str):
    """Verify an OTP under ``bot_id`` (see ``create_otp``)."""
    otp = OtpAuthorization.validate_otp(bot_id=bot_id, user_id=payload_data["user_id"], otp=payload_data["otp"])
    if otp:
        return {"code": 200, "message": "OTP authorized successfully"}
    return {"code": 401, "message": "Invalid OTP"}


def generate_otp():
    return str(random.randint(100000, 999999))


def send_otp_in_email(payload_data):
    # Prepare MIME message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Emly Labs Verification Code"
    msg["From"] = EMAIL_FROM
    msg["To"] = payload_data["user_id"]

    content = EMAIL_OTP_TEMPLATE
    content = content.replace("{otp_code}", payload_data["otp"])
    content = content.replace("{email}", payload_data["user_id"])
    fist_name, _, _ = payload_data["user_id"].partition('@')
    content = content.replace("{first_name}", fist_name)

    part = MIMEText(content, "html")
    msg.attach(part)
    # Send email
    email_sender = EmailSender(
        smtp_server=EMAIL_SERVER,
        port=EMAIL_PORT,
        username=EMAIL_USER,
        password=EMAIL_PASSWORD
    )
    email_sender.send_email(msg)


async def handle_file_uploads(files: List[UploadFile], user_id: str, bot_id: str):
    """Process file uploads and save to disk under ``bot_id``.

    The Emly_Files row carries the bot scope so per-bot admin views
    only show their own attachments."""
    uploaded_files = []
    if files:
        for file in files:
            file_content = await file.read()

            emly_file = Emly_Files.insert_new_file(
                bot_id=bot_id,
                user_id=user_id,
                file_name=file.filename,
                file_type=file.content_type,
                file_size=len(file_content),
                size_bytes=len(file_content),
                mime_type=file.content_type,
            )

            user_dir = Path(DATA_DIR) / "attachments" / str(user_id) / str(emly_file.get("id"))
            user_dir.mkdir(parents=True, exist_ok=True)
            file_path = user_dir / safe_filename(file.filename)
            with open(file_path, "wb") as f:
                f.write(file_content)

            uploaded_files.append({
                "filename": file.filename,
                "content_type": file.content_type,
                "size": len(file_content),
                "path": str(file_path),   # ✅ store only path
            })
            await file.seek(0)
    return uploaded_files



def send_scheduled_email(email, user_id, form_data_dict, form_title, uploaded_files, page, prompt="", bot_id: str = ""):
        """
        Wrapper function for sending email that can be serialized by APScheduler.

        ``bot_id`` is passed through the scheduler payload so the
        deferred job continues to read/write under the same tenant
        scope as the original request.
        """
        user = EMLYUsers.get_user_by_id(bot_id, user_id)

        request_date = datetime.now().strftime("%Y:%m:%d %H:%M:%S")
        subject = f"Emly Labs | {form_title or ''} | {request_date}"

        content, _ = get_html_content_with_summarized_messages(
            user,
            form_data_dict.get("session_id"),
            prompt,
            bot_id=bot_id,
            form_title=form_title or "",
        )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = email

        part = MIMEText(content, "html")
        msg.attach(part)

        # ✅ Attach files from saved paths
        for file in uploaded_files:
            try:
                with open(file["path"], "rb") as f:
                    file_content = f.read()
                file_attachment = MIMEApplication(file_content)
                file_attachment.add_header(
                    "Content-Disposition", "attachment", filename=file["filename"]
                )
                msg.attach(file_attachment)
            except Exception as e:
                print(f"⚠️ Could not attach file {file['filename']}: {e}")

        email_sender = EmailSender(
            smtp_server=EMAIL_SERVER,
            port=EMAIL_PORT,
            username=EMAIL_USER,
            password=EMAIL_PASSWORD,
        )
        email_sender.send_email(msg)

        EMLYMessages.insert_new_message(
            bot_id=bot_id,
            user_id=user_id,
            session_id=form_data_dict.get("session_id"),
            role="user",
            not_useful=False,
            message=f"I have submitted the form named {form_title} with the following details:\n{user.meta}",
            expanded_query="call_back",
            page=page,
        )
        EMLYMessages.insert_new_message(
            bot_id=bot_id,
            user_id=user_id,
            session_id=form_data_dict.get("session_id"),
            role="assistant",
            not_useful=False,
            message="",
            expanded_query="call_back",
            page=page,
            # Phase 2 backfill: this is a synthetic assistant turn — no
            # LLM ran. Tag the model so cost/latency aggregations don't
            # silently absorb form replies into per-model averages.
            model_used="form_handler",
        )



async def send_email_with_attachments(email: str, user, form_data, form_title, uploaded_files, page, prompt="", bot_id: str = ""):
    """Prepare and send email with attachments under ``bot_id``."""
    # Convert form_data to a dictionary to make it serializable
    form_data_dict = form_data.dict() if hasattr(form_data, 'dict') else form_data

    # Check initial message count
    message_count = len(EMLYMessages.get_messages_v2(
        bot_id=bot_id,
        user_id=user.id,
        session_id=form_data.session_id
    ))

    log.info(f"Messages count is {message_count} for user {user.id} and session {form_data.session_id}")

    if message_count == 0:
        # If no messages, schedule with a deferred time
        scheduler.add_job(
            send_scheduled_email,
            run_date=datetime.now(TIMEZONE) + timedelta(minutes=EMAIL_DELAY_IN_MINUTES),
            args=[
                email,
                user.id,  # Pass user ID instead of user object
                form_data_dict,
                form_title,
                uploaded_files,
                page,
                prompt,
                bot_id,
            ]
        )
    else:
        # If messages exist, send immediately
        scheduler.add_job(
            send_scheduled_email,
            args=[
                email,
                user.id,  # Pass user ID instead of user object
                form_data_dict,
                form_title,
                uploaded_files,
                page,
                prompt,
                bot_id,
            ]
        )


def get_html_content_with_summarized_messages(
    user, session_id, prompt: str = "", bot_id: str = "", form_title: str = ""
):
    """Retrieve latest messages and generate a summary, scoped to ``bot_id``.

    Returns ``(html_email, summary_text)``. The email is a self-contained
    HTML document with a branded header, summary, a CTA to the admin
    conversation page (view / update / resolve), the full transcript,
    and the user's details.
    """
    messages = EMLYMessages.get_messages_v2(
        bot_id=bot_id,
        user_id=user.id,
        session_id=session_id,
    )

    summarized_prompt = ""
    if messages:
        try:
            ordered_for_summary = list(reversed(messages))
            summarizer = OpenAIAgent(
                api_key=OPENAI_API_KEY,
                model=MODEL,
                endpoint=OPENAI_BASE_URL,
                last_n_message=ordered_for_summary,
                prompt_template=prompt,
            )
            summarized_prompt = summarizer.summarize_and_recommend(
                summarizer.create_summary_prompt()
            )
        except Exception:
            log.exception("Failed to summarize conversation")

    summary_html = _normalize_summary_to_html(summarized_prompt)

    user_details = (
        user.model_dump() if hasattr(user, "model_dump") else dict(user.__dict__)
    )

    content = _render_email_html(
        user_details=user_details,
        messages=messages,
        summary_html=summary_html,
        conversation_url=_build_conversation_url(bot_id, session_id or ""),
        form_title=form_title,
        request_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return content, summarized_prompt


@lru_cache
def cached_html_content_with_summarized_messages(bot_id: str, user_id, session_id, created_on):
    user = EMLYUsers.get_user_by_id(bot_id, user_id)
    content, _ = get_html_content_with_summarized_messages(user, session_id, bot_id=bot_id)
    return content


def get_last_message_timestamp(bot_id: str, user_id: str, session_id: str) -> int:
    last_message = EMLYMessages.get_messages(bot_id=bot_id, user_id=user_id, session_id=session_id, skip=0, limit=1)
    return last_message[0].created_on.timestamp() if last_message else time.time()


@router.get("/api/v1/usr/{user_id}/{session_id}")
def get_user_session_report_in_html(user_id: str, session_id: str, request: Request):
    bot_id = _bot_id_from_request(request)
    user = EMLYUsers.get_user_by_id(bot_id, user_id)
    if not user:
        return HTMLResponse(status_code=404, content="<h1>No Conversation yet</h1>")

    last_message_timestamp = get_last_message_timestamp(bot_id, user_id, session_id)
    content = cached_html_content_with_summarized_messages(bot_id, user_id, session_id, last_message_timestamp)
    return HTMLResponse(content=content, status_code=200)


@router.put("/api/v1/update/impression")
def update_impressions(request: Request, short_impression: bool = Query(None), long_impression: bool = Query(None)):
    try:
        bot_id = _bot_id_from_request(request)
        if not short_impression and not long_impression:
            return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"status": "failed",
                                                                                  "message": "At least one of 'short_impression' or 'long_impression' must be provided"})
        if short_impression:
            Bot_Impressions.insert_impression(bot_id=bot_id, impression_type="SHORT")
        if long_impression:
            Bot_Impressions.insert_impression(bot_id=bot_id, impression_type="LONG")
        return JSONResponse(status_code=status.HTTP_200_OK,
                            content={"status": "success", "message": "Impressions updated"})
    except HTTPException:
        raise
    except Exception:
        log.exception("Unexpected error in impression handler")
        raise HTTPException(status_code=500, detail="Unexpected error")


