"""Public widget-support endpoints (Tier 3 of multi-bot-ui plan).

The customer-site widget needs two non-chat surfaces:

- ``GET /widget/{bot_id}/config`` — pull theme / welcome / launcher
  styling from ``bots.config_json.widget`` so admins can customize the
  embed without redeploying ``widget.js``. v1 widgets that pass
  ``data-*`` attributes still work; v2+ widgets fetch this on mount and
  merge it under any per-script overrides.
- ``POST /widget/{bot_id}/action`` — form / lead submissions and OTP
  flows. Bot-scoped: the action row, the end-user row, and any
  integration dispatch all carry the bot's id from the URL. Email
  notification recipients are resolved server-side from
  ``c_forms_selected[*].trigger.alert_emails`` — the widget never sees
  or sends recipient addresses.

Both endpoints are public (no admin JWT) — the widget runs on the
customer's site. End-user identity comes from ``X-Emly-UserID`` /
``X-Emly-SessionID`` headers, same as ``/widget/{bot_id}/chat``.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from models.bot_impressions import Bot_Impressions
from models.bots import BotModel, Bots
from models.emly_messages import EMLYMessages
from models.emly_user_action import EMLYUserActionsFormData, USER_ACTIONS
from models.emly_users import EMLYUsers, EMLYUserUpdateForm
from services.bot_config import get_config_for_bot

router = APIRouter()
log = logging.getLogger(__name__)

# Hard ceiling for files per request, regardless of per-bot config.
# The per-bot ``LimitsConfig.file_count_cap`` is a soft total (lifetime
# files), this is just to keep one HTTP request small.
_PER_REQUEST_FILE_CAP = 10
# Absolute byte ceiling per file (fallback when LimitsConfig is loose).
_ABSOLUTE_BYTE_CAP = 100 * 1024 * 1024  # 100 MB

# A scheme an `<img src>` / `<iframe src>` can deref without executing
# a script. We only allow these for admin-supplied logo/avatar URLs in
# the widget config response — defense in depth even if widget.js
# already validates client-side.
_SAFE_URL_RE = re.compile(r"^(https?://|/)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Bot resolution (slug or id) — shared by widget routes and chat.widget_chat
# ---------------------------------------------------------------------------
def resolve_bot(bot_ref: str) -> BotModel:
    """Look up an active, non-deleted bot by id first (the widget
    snippet bakes ``bot.id``), falling back to slug for human-typed
    test URLs.

    Used by every public widget surface (``/widget/{ref}/{config,action,chat}``)
    so the lookup order is consistent — same string always resolves to
    the same bot regardless of which endpoint the caller hits."""
    bot = Bots.get_by_id(bot_ref) or Bots.get_by_slug(bot_ref)
    if bot is None or bot.is_deleted or not bot.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Bot not found: {bot_ref}")
    return bot


# Backwards-compatibility alias for existing callsites in this module.
_resolve_bot = resolve_bot


def _safe_url(value: Optional[str]) -> Optional[str]:
    """Strip URLs the widget shouldn't be allowed to load.

    Admins paste the logo/avatar URL via the structured config editor;
    a malicious admin could put ``javascript:...`` or ``data:text/html...``
    there. Force ``http(s)://`` or root-relative ``/foo``."""
    if not value:
        return None
    return value if _SAFE_URL_RE.match(value) else None


# ---------------------------------------------------------------------------
# GET /widget/{bot_id}/config — public theme/welcome blob
# ---------------------------------------------------------------------------
class WidgetTheme(BaseModel):
    launcher_background: Optional[str] = None
    header_background: Optional[str] = None
    header_foreground: Optional[str] = None
    user_message_background: Optional[str] = None
    user_message_foreground: Optional[str] = None
    bot_message_background: Optional[str] = None
    bot_message_foreground: Optional[str] = None
    container_background: Optional[str] = None


class WidgetConfigResponse(BaseModel):
    """Public-safe subset of the bot's config_json.

    Anything sensitive (api_key, integration credentials, raw topics
    with internal prompts) stays server-side. Only the customer-visible
    surface is exposed. ``c_forms_selected`` triggers are scrubbed of
    keys the LLM/email pipeline uses (``analysis_prompt``,
    ``alert_emails``, ``trigger_prompt``, ``integration``) before
    forwarding — the widget never needs them and they leak admin
    intent/recipients.
    """

    bot_id: str
    name: str
    title: Optional[str] = None
    subtitle: Optional[str] = None
    welcome_message: Optional[str] = None
    input_placeholder: Optional[str] = None
    logo: Optional[str] = None
    avatar: Optional[str] = None
    launcher_position: str = "right"
    chat_container_position: str = "right"
    open_on_load: bool = False
    theme: WidgetTheme = WidgetTheme()

    launcher_label: Optional[str] = None
    is_icon_with_label: Optional[bool] = None
    open_icon: Optional[str] = None
    close_icon: Optional[str] = None
    show_min_max: Optional[bool] = None
    show_close: Optional[bool] = None
    show_menu: Optional[bool] = None
    max_window: Optional[bool] = None
    open_link_in_same_tab: Optional[bool] = None
    feedback: Optional[bool] = None
    show_citations: Optional[bool] = None

    starter_messages: Optional[List[str]] = None
    nudges: Optional[Dict[str, Any]] = None
    c_forms_selected: Optional[List[Dict[str, Any]]] = None

    support_email: Optional[str] = None
    whatsapp_link: Optional[str] = None
    whatsapp_message: Optional[str] = None
    social_handles: Optional[Dict[str, Any]] = None
    terms_of_service: Optional[Dict[str, Any]] = None


# Trigger keys the widget never reads but that leak server-side intent
# (admin email lists, LLM prompts, integration IDs). Stripped per-form
# before ``c_forms_selected`` is sent down the wire.
_TRIGGER_PRIVATE_KEYS = frozenset({
    "analysis_prompt",
    "alert_emails",
    "trigger_prompt",
    "integration",
})


def _scrub_cforms(forms: Optional[List[Any]]) -> Optional[List[Dict[str, Any]]]:
    """Drop trigger keys that aren't customer-facing.

    ``c_forms_selected`` is a list of single-key dicts:
        ``[{<name>: {form_schema, trigger}}, ...]``
    We only touch ``trigger``; ``form_schema`` is already visitor-visible
    (it's literally the form they fill in)."""
    if not isinstance(forms, list):
        return None
    out: List[Dict[str, Any]] = []
    for entry in forms:
        if not isinstance(entry, dict):
            continue
        clean_entry: Dict[str, Any] = {}
        for name, body in entry.items():
            if not isinstance(body, dict):
                continue
            trigger = body.get("trigger")
            if isinstance(trigger, dict):
                trigger = {k: v for k, v in trigger.items() if k not in _TRIGGER_PRIVATE_KEYS}
            clean_entry[name] = {
                "form_schema": body.get("form_schema") or {},
                "trigger": trigger or {},
            }
        if clean_entry:
            out.append(clean_entry)
    return out or None


def _opt(value: Any, want_type: type) -> Any:
    """Return ``value`` only if it's the expected type, else ``None``.

    Defensive against admins (or legacy imports) leaving a stale string
    where a bool/list is expected."""
    return value if isinstance(value, want_type) else None


def _build_widget_config(bot: BotModel) -> WidgetConfigResponse:
    try:
        cfg = get_config_for_bot(bot.id)
    except LookupError:
        return WidgetConfigResponse(bot_id=bot.id, name=bot.name)

    raw = cfg.model_dump(mode="json") if cfg else {}
    widget_block = raw.get("widget") or {}
    theme_block = widget_block.get("theme") or {}
    welcome = (raw.get("global_prompts") or {}).get("welcome_message")

    return WidgetConfigResponse(
        bot_id=bot.id,
        name=bot.name,
        title=widget_block.get("title") or bot.name,
        subtitle=widget_block.get("subtitle"),
        welcome_message=widget_block.get("welcome_message") or welcome,
        input_placeholder=widget_block.get("input_placeholder"),
        logo=_safe_url(widget_block.get("logo")),
        avatar=_safe_url(widget_block.get("avatar")),
        launcher_position=widget_block.get("launcher_position") or "right",
        chat_container_position=widget_block.get("chat_container_position") or "right",
        open_on_load=bool(widget_block.get("open_on_load", False)),
        theme=WidgetTheme(**{k: v for k, v in theme_block.items() if v}),
        # Launcher chrome — admin-supplied URLs go through ``_safe_url``
        # for the same reason as ``logo``/``avatar``.
        launcher_label=_opt(raw.get("launcher_label"), str),
        is_icon_with_label=_opt(raw.get("is_icon_with_label"), bool),
        open_icon=_safe_url(_opt(raw.get("open_icon"), str)),
        close_icon=_safe_url(_opt(raw.get("close_icon"), str)),
        show_min_max=_opt(raw.get("show_min_max"), bool),
        show_close=_opt(raw.get("show_close"), bool),
        show_menu=_opt(raw.get("show_menu"), bool),
        max_window=_opt(raw.get("max_window"), bool),
        open_link_in_same_tab=_opt(raw.get("open_link_in_same_tab"), bool),
        feedback=_opt(raw.get("feedback"), bool),
        show_citations=_opt(raw.get("show_citations"), bool),
        starter_messages=_opt(raw.get("starter_messages"), list),
        nudges=_opt(raw.get("nudges"), dict),
        c_forms_selected=_scrub_cforms(raw.get("c_forms_selected")),
        support_email=_opt(raw.get("support_email"), str),
        whatsapp_link=_opt(raw.get("whatsapp_link"), str),
        whatsapp_message=_opt(raw.get("whatsapp_message"), str),
        social_handles=_opt(raw.get("social_handles"), dict),
        terms_of_service=_opt(raw.get("terms_of_service"), dict),
    )


@router.get("/widget/{bot_ref}/config", response_model=WidgetConfigResponse)
def widget_config(bot_ref: str):
    bot = _resolve_bot(bot_ref)
    return _build_widget_config(bot)


# ---------------------------------------------------------------------------
# GET /widget/{bot_ref}/submissions/counts — per-user form-submit tallies
# ---------------------------------------------------------------------------
class WidgetSubmissionCountsResponse(BaseModel):
    """Per-form-submit count for a single visitor of this bot.

    Powers the "N of M submissions remaining" footnote and the
    post-limit engagement bubble. The widget consults this on mount and
    after each successful submission. Public-safe: only counts the
    requesting visitor's own actions (filtered by the trusted
    ``X-Emly-UserID`` header — same header model as the chat surface)."""

    counts: Dict[str, int]


@router.get(
    "/widget/{bot_ref}/submissions/counts",
    response_model=WidgetSubmissionCountsResponse,
)
def widget_submission_counts(bot_ref: str, request: Request):
    bot = _resolve_bot(bot_ref)
    user_id = request.headers.get("X-Emly-UserID")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Emly-UserID header is required",
        )
    counts = USER_ACTIONS.submission_counts(bot.id, user_id)
    return WidgetSubmissionCountsResponse(counts=counts)


# ---------------------------------------------------------------------------
# POST /widget/{bot_id}/init — issue a widget HMAC token
# ---------------------------------------------------------------------------
class WidgetInitRequest(BaseModel):
    """Optional ``user_id`` lets a returning visitor keep their conversation
    history; if absent we generate a fresh ID and return it."""

    user_id: Optional[str] = None


class WidgetInitResponse(BaseModel):
    user_id: str
    session_id: str
    token: str
    ttl_seconds: int


# ---------------------------------------------------------------------------
# POST /widget/{bot_ref}/impressions — per-bot widget view tracking
# ---------------------------------------------------------------------------
class WidgetImpressionPayload(BaseModel):
    """Body for POST /widget/{ref}/impressions.

    `type` is `short` (widget mounted on the host page — passive view) or
    `long` (visitor opened the launcher — engaged view). The widget
    dedupes per-tab via sessionStorage so a single page load can't
    record more than one of each.
    """

    type: str


@router.post("/widget/{bot_ref}/impressions", status_code=status.HTTP_204_NO_CONTENT)
def widget_impression(bot_ref: str, payload: WidgetImpressionPayload):
    bot = _resolve_bot(bot_ref)
    kind = (payload.type or "").lower()
    if kind not in ("short", "long"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="type must be 'short' or 'long'",
        )
    Bot_Impressions.insert_impression(bot_id=bot.id, impression_type=kind.upper())


@router.post("/widget/{bot_ref}/init", response_model=WidgetInitResponse)
def widget_init(bot_ref: str, payload: Optional[WidgetInitRequest] = None):
    """Mint a per-bot HMAC token for a widget visitor.

    The widget calls this on mount; subsequent ``POST /widget/{ref}/chat``
    submissions will (Phase 7-followup) require the returned token in the
    ``Authorization: Bearer`` header rather than trusting the spoofable
    ``X-Emly-UserID`` header.
    """
    from services.auth.widget_hmac import _ttl_seconds, issue
    bot = _resolve_bot(bot_ref)
    requested_uid = payload.user_id if payload else None
    user_id = requested_uid or f"emly-gs-{uuid.uuid4()}"
    session_id = f"sess-{uuid.uuid4()}"
    ttl = _ttl_seconds()
    token = issue(
        bot_id=bot.id,
        user_id=user_id,
        session_id=session_id,
        key_version=bot.widget_key_version,
        ttl_seconds=ttl,
    )
    return WidgetInitResponse(
        user_id=user_id,
        session_id=session_id,
        token=token,
        ttl_seconds=ttl,
    )


# ---------------------------------------------------------------------------
# POST /widget/{bot_id}/action — form submissions / OTP
# ---------------------------------------------------------------------------
# Helpers from `routes.actions` are imported at module top — there's no
# circular-import pressure (actions doesn't import widget) and hoisting
# the import keeps each request handler from re-running the import
# machinery.
from routes.actions import (  # noqa: E402  — import after router/router-helpers for clarity
    authorize_otp,
    create_otp,
    handle_file_uploads,
    send_email_with_attachments,
)


def _validate_uploads(files: Optional[List[UploadFile]], bot: BotModel) -> None:
    """Reject obviously-abusive widget uploads before we read them.

    The widget ``action`` route is unauthenticated; without these caps
    a stranger can drive arbitrary disk writes against any known bot
    id. Per-bot ``LimitsConfig`` provides the size/MIME knobs admins
    can tighten.
    """
    if not files:
        return
    if len(files) > _PER_REQUEST_FILE_CAP:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Too many files in one request (max {_PER_REQUEST_FILE_CAP})",
        )

    try:
        cfg = get_config_for_bot(bot.id)
        limits = cfg.limits
    except LookupError:
        limits = None

    max_bytes = _ABSOLUTE_BYTE_CAP
    mime_allowlist: List[str] = []
    if limits is not None:
        max_bytes = min(int(limits.max_file_size_mb) * 1024 * 1024, _ABSOLUTE_BYTE_CAP)
        mime_allowlist = [m.lower() for m in (limits.mime_allowlist or [])]

    for f in files:
        # Starlette/FastAPI exposes ``size`` only when the client sent
        # a Content-Length per part. When absent we still cap during
        # ``handle_file_uploads`` (it reads ``await file.read()`` once
        # and the byte length is checked there), but rejecting
        # known-too-large uploads here is cheaper for the client.
        size = getattr(f, "size", None)
        if size is not None and size > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File '{f.filename}' exceeds {max_bytes // (1024 * 1024)} MB",
            )
        if mime_allowlist:
            content_type = (f.content_type or "").lower()
            if content_type not in mime_allowlist:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail=f"File '{f.filename}' has type '{f.content_type}' "
                    "which is not allowed by this bot's upload policy",
                )


# ---------------------------------------------------------------------------
# Phase 7 backend-backfill: end-user CSAT rating endpoint.
# ---------------------------------------------------------------------------
class WidgetRatePayload(BaseModel):
    """Body for POST /widget/{ref}/messages/{id}/rate."""

    rating: int  # -1, 0, +1
    session_id: str
    user_id: Optional[str] = None
    free_text: Optional[str] = None  # captured by the audit log only for v1


class WidgetRateResponse(BaseModel):
    ok: bool
    message_id: int
    rating: int


@router.post("/widget/{bot_ref}/messages/{message_id}/rate", response_model=WidgetRateResponse)
def widget_rate_message(
    bot_ref: str,
    message_id: int,
    payload: WidgetRatePayload,
):
    """Public rating endpoint for end users — backs CSAT.

    Verification model (v1):
        - The message must belong to (bot, session_id) the rater claims.
        - rating must be in {-1, 0, +1}.
        - Anyone with a valid (session_id, message_id) pair can rate.
          That's the same trust level as ``POST /widget/{ref}/chat`` today —
          a follow-up can plug in widget HMAC token verification once the
          embed bundle threads it through.
    """
    bot = _resolve_bot(bot_ref)
    if payload.rating not in (-1, 0, 1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="rating must be -1, 0, or 1",
        )
    msg = EMLYMessages.get_message_by_id(bot.id, message_id)
    if msg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    if msg.session_id != payload.session_id:
        # Don't leak which one mismatched — message vs session — to keep
        # the endpoint awkward to enumerate.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Message not in this session")
    if msg.role != "assistant":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only assistant messages can be rated",
        )
    if not EMLYMessages.set_rating(bot.id, message_id, rating=payload.rating):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Update failed")
    # Audit-log the rating event so admins can investigate misuse.
    try:
        from services.audit import audit
        audit(
            action="widget.message.rated",
            admin_id=None,
            bot_id=bot.id,
            target_type="message",
            target_id=str(message_id),
            payload={
                "rating": payload.rating,
                "session_id": payload.session_id,
                "user_id": payload.user_id,
                "free_text": payload.free_text,
            },
        )
    except Exception:
        log.debug("widget rate audit log failed", exc_info=True)
    return WidgetRateResponse(ok=True, message_id=message_id, rating=payload.rating)


@router.post("/widget/{bot_ref}/action")
async def widget_action(
    bot_ref: str,
    request: Request,
    payload: str = Form(...),
    files: List[UploadFile] = File(default=None),
):
    """Bot-scoped form-submission / OTP endpoint.

    Expects multipart/form-data with:
    - ``payload`` — JSON string with the action shape
      (``user_id``, ``session_id``, ``action_name``, ``action_value``,
      ``action_payload``, optional ``form_title``,
      ``first_name`` / ``email`` / etc. for end-user upsert).
    - ``files`` — optional file attachments.

    Notification recipients are resolved server-side from the matching
    ``c_forms_selected[*].trigger.alert_emails`` entry — never echoed
    by the widget — so admin recipient lists never leave the server.
    """
    bot = _resolve_bot(bot_ref)
    page = request.headers.get("X-Emly-PageID")

    # Reject abusive uploads (size / count / MIME) up front. The widget
    # is unauthenticated, so caps come from the bot's ``LimitsConfig``.
    _validate_uploads(files, bot)

    try:
        payload_data = _parse_payload(payload)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid payload JSON: {e}")

    form_title = (payload_data.get("form_title") or "").strip().lower()

    # OTP flows (auth_authentication / auth_verification) — handled by
    # the same helpers the legacy route uses, but bot-scoped.
    if form_title == "auth_authentication":
        if not payload_data.get("user_id"):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"status": "failed", "message": "user_id is required"},
            )
        create_otp(payload_data, bot_id=bot.id)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "success", "message": "Otp generated successfully"},
        )

    if form_title == "auth_verification":
        if not payload_data.get("user_id"):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"status": "failed", "message": "user_id is required"},
            )
        result = authorize_otp(payload_data, bot_id=bot.id)
        return JSONResponse(
            status_code=result["code"],
            content={"status": "success", "message": result["message"]},
        )

    # Generic action: upsert end-user, insert action row, optional email
    # / integration dispatch.
    user_payload = {
        "first_name": payload_data.get("first_name"),
        "last_name": payload_data.get("last_name"),
        "email": payload_data.get("email"),
        "phone": payload_data.get("phone"),
        "meta": payload_data.get("meta"),
        "country": payload_data.get("country"),
        "region": payload_data.get("region"),
        "city": payload_data.get("city"),
        "latitude": payload_data.get("latitude"),
        "longitude": payload_data.get("longitude"),
    }

    user = await _get_or_create_widget_user(
        bot_id=bot.id,
        request=request,
        user_id=payload_data.get("user_id"),
        user_payload=user_payload,
    )

    message_id = payload_data.get("message_id")
    if message_id:
        msg = EMLYMessages.get_message_by_id(bot.id, int(message_id)) if str(message_id).isdigit() else None
        if msg is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    user_action = USER_ACTIONS.insert_new_action(
        bot_id=bot.id,
        user_id=user.id,
        session_id=payload_data.get("session_id"),
        message_id=int(message_id) if (message_id and str(message_id).isdigit()) else None,
        action_name=payload_data.get("action_name"),
        action_value=payload_data.get("action_value"),
        action_payload=payload_data.get("action_payload"),
    )

    # Files are tagged with the URL-resolved bot.id, so admins of bot A
    # see them and admins of bot B don't.
    uploaded_files = await handle_file_uploads(files, user.id, bot_id=bot.id)

    recipients = _resolve_alert_recipients(bot.id, payload_data.get("form_title"))
    if recipients:
        form_data = EMLYUserActionsFormData(**{
            "user_id": user.id,
            "session_id": payload_data.get("session_id"),
            "message_id": message_id,
            "action_name": payload_data.get("action_name"),
            "action_value": payload_data.get("action_value"),
            "action_payload": payload_data.get("action_payload"),
        })
        for recipient in recipients:
            await send_email_with_attachments(
                recipient,
                user,
                form_data,
                payload_data.get("form_title"),
                uploaded_files,
                page,
                payload_data.get("prompt", ""),
                bot_id=bot.id,
            )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "success", "user_action": user_action},
    )


# ---------------------------------------------------------------------------
# Helpers (bot-scoped variants of legacy actions.py utilities)
# ---------------------------------------------------------------------------
def _parse_payload(payload: str) -> Dict[str, Any]:
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload is required")
    return json.loads(payload)


def _resolve_alert_recipients(bot_id: str, form_title: Optional[str]) -> List[str]:
    """Look up the configured ``alert_emails`` for a submitted form.

    Matches ``form_title`` (which the widget derives from
    ``form_schema.name`` or ``form_schema.id``) against each
    ``c_forms_selected`` entry's outer key, ``form_schema.name``, and
    ``form_schema.id``. ``alert_emails`` may be a list or a
    comma-separated string; both shapes are accepted.

    Recipients are server-only: ``_TRIGGER_PRIVATE_KEYS`` strips them
    from the public widget config, so the browser never sees them and
    can't be the source of truth here.
    """
    if not form_title:
        return []
    try:
        cfg = get_config_for_bot(bot_id)
    except LookupError:
        return []

    target = form_title.strip().lower()
    raw = cfg.model_dump(mode="json")
    for entry in raw.get("c_forms_selected") or []:
        if not isinstance(entry, dict):
            continue
        for name, body in entry.items():
            if not isinstance(body, dict):
                continue
            schema = body.get("form_schema") or {}
            keys = {
                str(name).strip().lower(),
                str(schema.get("id") or "").strip().lower(),
                str(schema.get("name") or "").strip().lower(),
            }
            if target not in keys:
                continue
            trigger = body.get("trigger") or {}
            return _coerce_email_list(trigger.get("alert_emails"))
    return []


def _coerce_email_list(value: Any) -> List[str]:
    """Normalize ``alert_emails`` (list | comma-string | None) to a clean list."""
    if isinstance(value, list):
        return [s.strip() for s in value if isinstance(s, str) and s.strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []


async def _get_or_create_widget_user(
    bot_id: str,
    request: Request,
    user_id: Optional[str],
    user_payload: Optional[Dict[str, Any]] = None,
):
    """Bot-scoped end-user upsert. Threads the bot id from the URL.

    Tolerant of the absent-client case: ``request.client`` can be
    ``None`` when an upstream proxy strips the connection metadata
    or under TestClient — fall back to empty strings rather than
    crashing on ``NoneType.host``."""
    user_payload = user_payload or {}
    if user_id is None:
        user_id = f"emly-gs-{uuid.uuid4()}"

    client = request.client
    client_host = client.host if client else ""
    client_port = client.port if client else None
    user_agent = request.headers.get("user-agent", "")

    existing = EMLYUsers.get_user_by_id(bot_id, user_id)
    if existing is None:
        meta = user_payload.get("meta") or (
            {"host": client_host, "port": client_port} if client else {}
        )
        try:
            return EMLYUsers.insert_new_user(
                bot_id=bot_id,
                id=user_id,
                first_name=user_payload.get("first_name"),
                last_name=user_payload.get("last_name"),
                phone=user_payload.get("phone"),
                email=user_payload.get("email"),
                ip=client_host,
                browser=user_agent,
                meta=meta,
                country=user_payload.get("country"),
                region=user_payload.get("region"),
                city=user_payload.get("city"),
                longitude=user_payload.get("longitude"),
                latitude=user_payload.get("latitude"),
            )
        except Exception:
            # Race: a concurrent first request from the same end user
            # may have inserted the row in the gap between our
            # ``get_user_by_id`` and ``insert_new_user``. Re-fetch
            # rather than treating the PK conflict as an error.
            log.info("Concurrent insert for bot=%s user=%s; re-reading", bot_id, user_id)
            existing = EMLYUsers.get_user_by_id(bot_id, user_id)
            if existing is None:
                raise

    # Update mutable fields if provided.
    if any(user_payload.get(k) for k in ("first_name", "last_name", "email", "phone")):
        EMLYUsers.update_user(bot_id, user_id, EMLYUserUpdateForm(**user_payload))
    return EMLYUsers.get_user_by_id(bot_id, user_id)
