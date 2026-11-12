"""Widget email rendering and dispatch (post-form-submission).

Renders a branded HTML email with summary, transcript, and user
details; sends immediately or schedules via APScheduler.
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
from datetime import datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import lru_cache
from typing import Any

import mistune

from config import (
    EMAIL_DELAY_IN_MINUTES,
    EMAIL_FROM,
    EMAIL_PASSWORD,
    EMAIL_PORT,
    EMAIL_SERVER,
    EMAIL_USER,
    MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    SUMMARY_RECOMMENDATION_PROMPT,
    TIMEZONE,
)
from models.bots import Bots
from models.emly_messages import EMLYMessages
from models.emly_users import EMLYUsers
from services.bot_config import get_decrypted_api_key
from services.email_service import EmailSender
from services.scheduler import scheduler

log = logging.getLogger(__name__)


_EMAIL_BRAND_COLOR = "#0F62FE"
_EMAIL_BRAND_DARK = "#0043CE"
_EMAIL_TEXT = "#1F2937"
_EMAIL_MUTED = "#6B7280"
_EMAIL_BORDER = "#E5E7EB"
_EMAIL_BG = "#F3F4F6"
_EMAIL_CARD_BG = "#FFFFFF"
_EMAIL_BOT_BG = "#F9FAFB"
_EMAIL_USER_BG = "#EFF6FF"


def clean_key(key: str) -> str:
    return " ".join(word.capitalize() for word in key.replace("_", " ").split())


def _html_escape(value: Any) -> str:
    return _html.escape("" if value is None else str(value))


def _normalize_summary_to_html(raw: str) -> str:
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
    rows: list[tuple[str, Any]] = []
    meta_rows: list[tuple[str, Any]] = []
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

    def _row(k: str, v: Any) -> str:
        return (
            "<tr>"
            f'<td style="padding:8px 12px;border-bottom:1px solid {_EMAIL_BORDER};'
            f'color:{_EMAIL_MUTED};font-size:13px;width:40%;">{_html_escape(k)}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid {_EMAIL_BORDER};'
            f'color:{_EMAIL_TEXT};font-size:14px;">{_html_escape(v)}</td>'
            "</tr>"
        )

    return (
        "".join(_row(k, v) for k, v in rows),
        "".join(_row(k, v) for k, v in meta_rows),
    )


def _render_transcript(messages) -> str:
    if not messages:
        return (
            f'<p style="margin:0;color:{_EMAIL_MUTED};font-size:14px;">'
            "No messages were exchanged in this session."
            "</p>"
        )

    md = mistune.create_markdown()
    bubbles: list[str] = []
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
            f"</table></td></tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        + "".join(bubbles)
        + "</table>"
    )


def _build_conversation_url(bot_id: str, session_id: str) -> str:
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
            f"text-decoration:none;padding:12px 22px;border-radius:8px;"
            f'font-size:14px;font-weight:600;display:inline-block;">'
            f"View, update &amp; resolve conversation"
            f"</a>"
            f'</td></tr><tr><td style="padding-top:8px;color:{_EMAIL_MUTED};'
            f'font-size:12px;">Opens the admin console where you can review the '
            f"full thread, edit details, and mark it resolved.</td></tr></table>"
        )

    summary_section = ""
    if summary_html:
        summary_section = (
            f'<h2 style="margin:0 0 12px 0;color:{_EMAIL_TEXT};font-size:16px;'
            f'font-weight:600;">Summary</h2>'
            f'<div style="color:{_EMAIL_TEXT};font-size:14px;line-height:1.6;">'
            f"{summary_html}</div>"
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
            f"{user_rows}"
            + (
                f'<tr><td colspan="2" style="padding:10px 12px;background:{_EMAIL_BG};'
                f'color:{_EMAIL_MUTED};font-size:12px;text-transform:uppercase;'
                f'letter-spacing:.04em;border-bottom:1px solid {_EMAIL_BORDER};">'
                f"Additional metadata</td></tr>{meta_rows}"
                if meta_rows
                else ""
            )
            + "</table>"
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


def _summarize_conversation(bot_id: str, messages, prompt_template: str) -> str:
    """Generate the human-agent-facing summary using the bot's own LLM creds.

    Why: per-bot keys live encrypted in ``bots.api_key_encrypted`` and the
    chat model in ``bots.config_json.llm.model``; the deployment-wide env
    fallbacks alone won't work for tenants that bring their own key.
    """
    from langchain_openai import ChatOpenAI

    conversation_text = "\n".join(
        f"{m.role}: {m.message}" for m in reversed(messages)
    )
    instructions = prompt_template.strip() if prompt_template else SUMMARY_RECOMMENDATION_PROMPT
    prompt = f"Here are the most recent conversations:\n\n{conversation_text}\n\n{instructions}"

    model = MODEL
    base_url = OPENAI_BASE_URL
    if bot_id:
        try:
            bot = Bots.get_by_id(bot_id)
            llm_cfg = (bot.config_json or {}).get("llm") if bot else None
            if llm_cfg:
                if llm_cfg.get("model"):
                    model = llm_cfg["model"]
                if llm_cfg.get("api_endpoint"):
                    base_url = llm_cfg["api_endpoint"]
        except Exception:
            log.exception("Failed to read per-bot LLM config for bot_id=%s", bot_id)

    api_key = get_decrypted_api_key(bot_id) if bot_id else None
    api_key = api_key or OPENAI_API_KEY
    if not api_key:
        log.warning("No LLM API key available for bot_id=%s; skipping summary", bot_id)
        return ""

    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": 0.3,
        "timeout": 30,
        "max_retries": 1,
        "api_key": api_key,
    }
    if base_url:
        kwargs["base_url"] = base_url

    result = ChatOpenAI(**kwargs).invoke(prompt)
    return getattr(result, "content", None) or ""


def get_html_content_with_summarized_messages(
    user, session_id, prompt: str = "", bot_id: str = "", form_title: str = ""
):
    messages = EMLYMessages.get_messages_v2(
        bot_id=bot_id, user_id=user.id, session_id=session_id
    )

    summarized_prompt = ""
    if messages:
        try:
            summarized_prompt = _summarize_conversation(bot_id, messages, prompt)
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
def cached_html_content_with_summarized_messages(bot_id: str, user_id, session_id, created_at):
    user = EMLYUsers.get_user_by_id(bot_id, user_id)
    content, _ = get_html_content_with_summarized_messages(user, session_id, bot_id=bot_id)
    return content


def get_last_message_timestamp(bot_id: str, user_id: str, session_id: str) -> int:
    last_message = EMLYMessages.get_messages(
        bot_id=bot_id, user_id=user_id, session_id=session_id, skip=0, limit=1
    )
    import time
    return last_message[0].created_at.timestamp() if last_message else time.time()


def send_scheduled_email(
    email,
    user_id,
    form_data_dict,
    form_title,
    uploaded_files,
    page,
    prompt: str = "",
    bot_id: str = "",
) -> None:
    """Deferred-email worker registered via APScheduler.

    ``bot_id`` rides the scheduler payload so the job continues to
    read/write under the same tenant scope as the original request.
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
    msg.attach(MIMEText(content, "html"))

    for file in uploaded_files:
        try:
            with open(file["path"], "rb") as f:
                file_content = f.read()
            attachment = MIMEApplication(file_content)
            attachment.add_header(
                "Content-Disposition", "attachment", filename=file["filename"]
            )
            msg.attach(attachment)
        except Exception:
            log.warning("Could not attach file %s", file.get("filename"), exc_info=True)

    EmailSender(
        smtp_server=EMAIL_SERVER,
        port=EMAIL_PORT,
        username=EMAIL_USER,
        password=EMAIL_PASSWORD,
    ).send_email(msg)

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
        model_used="form_handler",
    )


async def send_email_with_attachments(
    email: str,
    user,
    form_data,
    form_title,
    uploaded_files,
    page,
    prompt: str = "",
    bot_id: str = "",
) -> None:
    form_data_dict = form_data.dict() if hasattr(form_data, "dict") else form_data

    message_count = len(
        EMLYMessages.get_messages_v2(
            bot_id=bot_id, user_id=user.id, session_id=form_data.session_id
        )
    )
    log.info(
        "Messages count is %s for user %s session %s", message_count, user.id, form_data.session_id
    )

    args = [
        email,
        user.id,
        form_data_dict,
        form_title,
        uploaded_files,
        page,
        prompt,
        bot_id,
    ]
    if message_count == 0:
        scheduler.add_job(
            send_scheduled_email,
            run_date=datetime.now(TIMEZONE) + timedelta(minutes=EMAIL_DELAY_IN_MINUTES),
            args=args,
        )
    else:
        scheduler.add_job(send_scheduled_email, args=args)
