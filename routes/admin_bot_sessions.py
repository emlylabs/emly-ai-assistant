"""Phase 5 backend-backfill — per-bot session aggregation routes.

The admin Conversations UI used to fetch ~200 messages and group by
``session_id`` client-side. Now that ``emly_session`` carries one row
per session (Phase 4), the UI hits these endpoints instead and gets
proper pagination, channel filtering, and resolution status straight
from the DB.

Auth model mirrors ``routes/admin_bot_dashboard.py``: any role on the
bot can read; mutations (Phase 6 resolve/escalate, Phase 10 takeover)
require owner/admin.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from models.admin_bot_memberships import AdminBotMemberships
from models.admin_users import AdminUserModel
from models.bot_channels import BotChannels
from models.bots import BotModel, Bots
from models.emly_messages import EMLYMessageModel, EMLYMessages
from models.emly_sessions import EMLYSessionModel, EMLYSessions
from services.audit import audit
from services.auth.dependencies import get_admin

log = logging.getLogger(__name__)
router = APIRouter()


def _resolve_bot(slug: str) -> BotModel:
    bot = Bots.get_by_slug(slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    return bot


def _require_member(admin_id: str, bot_id: str) -> str:
    m = AdminBotMemberships.get(admin_id, bot_id)
    if m is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this bot")
    return m.role


def _require_writer(admin_id: str, bot_id: str) -> None:
    role = _require_member(admin_id, bot_id)
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Read-only role on this bot")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SessionRow(BaseModel):
    """List response row — `EMLYSessionModel` plus the latest message preview
    so the UI can render the conversation list without a second fetch."""

    session: EMLYSessionModel
    last_message: Optional[EMLYMessageModel] = None


class SessionListResponse(BaseModel):
    items: List[SessionRow]
    total: int
    skip: int
    limit: int


class SessionDetailResponse(BaseModel):
    session: EMLYSessionModel
    messages: List[EMLYMessageModel]


class ResolvePayload(BaseModel):
    is_resolved: bool = True
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# List & detail
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Daily aggregation for the analytics page
# ---------------------------------------------------------------------------
class DailySessionBucket(BaseModel):
    """One row per UTC day in the requested window. ``started`` counts
    sessions whose ``started_at`` falls in the day; ``resolved`` counts
    sessions whose ``resolved_at`` does. Days with no activity are
    included with zero counts so the UI can plot a continuous series."""

    day: str  # ISO YYYY-MM-DD
    started: int
    resolved: int


@router.get("/bots/{slug}/sessions/daily", response_model=List[DailySessionBucket])
def list_sessions_daily(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    channel_id_q: Optional[str] = Query(
        None,
        alias="channel_id",
        description="Restrict to a single `bot_channel.id`.",
    ),
    admin: AdminUserModel = Depends(get_admin),
):
    """Per-day session counts for the analytics page's flow chart.

    The window is bounded — a full open-ended scan would need a cursor.
    For the analytics page's 90-day max range, in-process bucketing of
    the result set is comfortably under a millisecond per thousand rows.
    """
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    from datetime import timedelta

    from models.emly_sessions import EMLYSession

    # Naive-UTC datetimes (matches ``EMLYSession.started_at`` invariant).
    end = datetime.utcfromtimestamp(to_ts) if to_ts else datetime.utcnow()
    start = datetime.utcfromtimestamp(from_ts) if from_ts else (end - timedelta(days=30))
    if end < start:
        raise HTTPException(status_code=400, detail="`to` must be on or after `from`")
    span_days = (end.date() - start.date()).days + 1
    if span_days > 366:
        # 366 covers the 90-day analytics range with ample headroom; the
        # cap is here so a malformed query doesn't fan out unbounded.
        raise HTTPException(status_code=400, detail="Window too wide (max 366 days)")

    started_counts: dict[str, int] = {}
    resolved_counts: dict[str, int] = {}

    # `started_at` falls in the window — these are sessions that were
    # opened during the period.
    started_q = EMLYSession.select(EMLYSession.started_at).where(
        (EMLYSession.bot_id == bot.id)
        & (EMLYSession.started_at >= start)
        & (EMLYSession.started_at <= end)
    )
    if channel_id_q:
        started_q = started_q.where(EMLYSession.channel_id == channel_id_q)
    for row in started_q.dicts():
        ts = row["started_at"]
        if ts is None:
            continue
        key = ts.date().isoformat()
        started_counts[key] = started_counts.get(key, 0) + 1

    # `resolved_at` falls in the window — independent of `started_at`,
    # since a long-running session may resolve days after it started.
    resolved_q = EMLYSession.select(EMLYSession.resolved_at).where(
        (EMLYSession.bot_id == bot.id)
        & (EMLYSession.resolved_at.is_null(False))
        & (EMLYSession.resolved_at >= start)
        & (EMLYSession.resolved_at <= end)
    )
    if channel_id_q:
        resolved_q = resolved_q.where(EMLYSession.channel_id == channel_id_q)
    for row in resolved_q.dicts():
        ts = row["resolved_at"]
        if ts is None:
            continue
        key = ts.date().isoformat()
        resolved_counts[key] = resolved_counts.get(key, 0) + 1

    # Emit a continuous series so the UI can render straight onto the
    # chart without a second pass to fill gaps.
    out: List[DailySessionBucket] = []
    cursor = start.date()
    end_date = end.date()
    while cursor <= end_date:
        key = cursor.isoformat()
        out.append(
            DailySessionBucket(
                day=key,
                started=started_counts.get(key, 0),
                resolved=resolved_counts.get(key, 0),
            )
        )
        cursor += timedelta(days=1)
    return out


@router.get("/bots/{slug}/sessions", response_model=SessionListResponse)
def list_sessions(
    slug: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    channel_id: Optional[str] = Query(None),
    is_resolved: Optional[bool] = Query(None),
    session_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    started_after: Optional[datetime] = Query(None),
    started_before: Optional[datetime] = Query(None),
    rating: Optional[str] = Query(
        None,
        pattern="^(rated|unrated|positive|negative)$",
        description="Filter by rating state of any message in the session.",
    ),
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    if started_after is not None and started_before is not None and started_after > started_before:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`started_after` must be on or before `started_before`",
        )
    filter_kwargs = dict(
        channel_id=channel_id,
        is_resolved=is_resolved,
        session_id=session_id,
        user_id=user_id,
        started_after=started_after,
        started_before=started_before,
        rating=rating,
    )
    rows = EMLYSessions.list_for_bot(bot.id, skip=skip, limit=limit, **filter_kwargs)
    total = EMLYSessions.count_for_bot(bot.id, **filter_kwargs)
    items: List[SessionRow] = []
    for s in rows:
        last = None
        try:
            paged = EMLYMessages.list_all(bot.id, session_id=s.id, skip=0, limit=1)
            if paged:
                last = paged[0]
        except Exception:
            log.exception("list_sessions: failed to fetch last message for %s", s.id)
        items.append(SessionRow(session=s, last_message=last))
    return SessionListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/bots/{slug}/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session(
    slug: str,
    session_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    session = EMLYSessions.get(bot.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    # Fetch messages — `list_all` returns newest-first; reverse for the
    # thread view which renders top-to-bottom oldest-first. For now pull
    # up to 500 turns; larger sessions get a follow-up paging param.
    messages = EMLYMessages.list_all(bot.id, session_id=session_id, skip=0, limit=500)
    messages = list(reversed(messages or []))
    return SessionDetailResponse(session=session, messages=messages)


# ---------------------------------------------------------------------------
# Mutations (Phase 6 — resolution / escalation)
# ---------------------------------------------------------------------------
@router.post("/bots/{slug}/sessions/{session_id}/resolve", response_model=EMLYSessionModel)
def resolve_session(
    slug: str,
    session_id: str,
    payload: ResolvePayload,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    if EMLYSessions.get(bot.id, session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    EMLYSessions.mark_resolved(
        bot.id,
        session_id,
        resolved_by=admin.id,
        is_resolved=payload.is_resolved,
    )
    audit(
        action="session.resolved" if payload.is_resolved else "session.reopened",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="session",
        target_id=session_id,
        payload={"reason": payload.reason} if payload.reason else None,
    )
    updated = EMLYSessions.get(bot.id, session_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Session vanished after update")
    return updated


@router.post("/bots/{slug}/sessions/{session_id}/escalate", response_model=EMLYSessionModel)
def escalate_session(
    slug: str,
    session_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    """Companion to /resolve — explicitly mark the session unresolved/escalated.

    Distinct from "reopen": escalation is a forward state for support tooling
    (a human picked it up). Reopen would call /resolve with is_resolved=false.
    """
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    if EMLYSessions.get(bot.id, session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    EMLYSessions.mark_resolved(
        bot.id, session_id, resolved_by=admin.id, is_resolved=False
    )
    audit(
        action="session.escalated",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="session",
        target_id=session_id,
    )
    updated = EMLYSessions.get(bot.id, session_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Session vanished after update")
    return updated


# ---------------------------------------------------------------------------
# Phase 10 take-over endpoints (web_widget only in v1)
# ---------------------------------------------------------------------------
class TakeoverReplyPayload(BaseModel):
    message: str


def _channel_kind_or_none(channel_id: Optional[str]) -> Optional[str]:
    if not channel_id:
        return None
    ch = BotChannels.get_by_id(channel_id)
    return ch.type if ch is not None else None


@router.post("/bots/{slug}/sessions/{session_id}/takeover", response_model=EMLYSessionModel)
def takeover_session(
    slug: str,
    session_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    """Pause the bot on this session and claim it for the calling admin.

    Phase 10 v1: only web_widget sessions are supported. Other channel
    kinds return 400 with a documented `reason` body so the UI can render
    a clear "Channel adapter pending" tooltip.
    """
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    session = EMLYSessions.get(bot.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    kind = _channel_kind_or_none(session.channel_id)
    if kind is not None and kind != "web_widget":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "channel_adapter_pending",
                "channel": kind,
                "message": (
                    f"Take-over for the '{kind}' channel hasn't shipped yet. "
                    "v1 supports web_widget sessions only; per-adapter follow-ups "
                    "extend Slack/Teams/Telegram/WhatsApp/Google Chat."
                ),
            },
        )
    EMLYSessions.set_takeover(bot.id, session_id, admin_id=admin.id)
    audit(
        action="session.takeover.claimed",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="session",
        target_id=session_id,
    )
    try:
        from services.realtime import publish
        publish(bot.id, {
            "type": "session_takeover",
            "bot_id": bot.id,
            "session_id": session_id,
            "taken_over_by": admin.id,
        })
    except Exception:
        log.debug("realtime publish (takeover) failed", exc_info=True)
    updated = EMLYSessions.get(bot.id, session_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Session vanished after update")
    return updated


@router.post("/bots/{slug}/sessions/{session_id}/release", response_model=EMLYSessionModel)
def release_session(
    slug: str,
    session_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    """Release a take-over so the bot resumes responding."""
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    if EMLYSessions.get(bot.id, session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    EMLYSessions.set_takeover(bot.id, session_id, admin_id=None)
    audit(
        action="session.takeover.released",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="session",
        target_id=session_id,
    )
    try:
        from services.realtime import publish
        publish(bot.id, {
            "type": "session_release",
            "bot_id": bot.id,
            "session_id": session_id,
        })
    except Exception:
        log.debug("realtime publish (release) failed", exc_info=True)
    updated = EMLYSessions.get(bot.id, session_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Session vanished after update")
    return updated


@router.post("/bots/{slug}/sessions/{session_id}/reply", response_model=EMLYMessageModel)
def admin_reply(
    slug: str,
    session_id: str,
    payload: TakeoverReplyPayload,
    admin: AdminUserModel = Depends(get_admin),
):
    """Inject a human-authored reply into a session that the admin owns
    via take-over. Persisted as a normal assistant message with a special
    ``model_used`` tag so analytics can separate human replies from bot
    replies.
    """
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    session = EMLYSessions.get(bot.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.taken_over_by != admin.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session is not under your active take-over",
        )
    if not payload.message or not payload.message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message body required")
    row = EMLYMessages.insert_new_message(
        bot_id=bot.id,
        user_id=session.user_id or "",
        session_id=session_id,
        role="assistant",
        message=payload.message,
        not_useful=False,
        expanded_query=None,
        page=None,
        topic=None,
        channel_id=session.channel_id,
        model_used=f"admin:{admin.id}",
        is_deflected=False,
        deflection_method="admin",
    )
    if row is None:
        raise HTTPException(status_code=500, detail="Reply persistence failed")
    audit(
        action="session.takeover.reply",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="session",
        target_id=session_id,
        payload={"message_id": row.id},
    )
    try:
        from services.realtime import publish
        publish(bot.id, {
            "type": "admin_reply",
            "bot_id": bot.id,
            "session_id": session_id,
            "message_id": row.id,
        })
    except Exception:
        log.debug("realtime publish (admin_reply) failed", exc_info=True)
    return EMLYMessages.get_message_by_id(bot.id, row.id)
