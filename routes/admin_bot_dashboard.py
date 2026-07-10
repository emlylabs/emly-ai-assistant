"""Slug-scoped admin views (Tier 1 of multi-bot UI execution).

Per-bot dashboard / messages / end-users / config. The legacy global
routes in `routes/admin_dashboard.py` and `routes/actions.py` stay
for backwards compat (deprecated); UI calls land here instead.

Authz model:
- Read routes accept any role (owner / admin / viewer) on the bot.
- Mutating routes require owner-or-admin (DB-rechecked).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel, Field

from models.admin_bot_memberships import AdminBotMemberships, ROLES
from models.admin_users import AdminUserModel, AdminUsers
from models.bots import BotModel, Bots
from models.emly_messages import EMLYMessageModel, EMLYMessages
from models.emly_users import EMLYUserModel, EMLYUsers
from services.auth.dependencies import get_admin
from services.bot_config import (
    ActiveBotConfig,
    get_config_for_bot,
    save_config_for_bot,
    set_api_key,
)

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers (mirror admin_bots / admin_bot_files)
# ---------------------------------------------------------------------------
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
class BotDashboardStats(BaseModel):
    bot_id: str
    slug: str
    name: str
    end_user_count: int
    message_count: int
    file_count: int
    member_count: int


class MessageListResponse(BaseModel):
    items: List[EMLYMessageModel]
    total: int
    skip: int
    limit: int


class UserListResponse(BaseModel):
    items: List[EMLYUserModel]
    total: int
    skip: int
    limit: int


class UpdateMessageRequest(BaseModel):
    not_useful: Optional[bool] = None


class ConfigPayload(BaseModel):
    config: Dict[str, Any]
    expected_version: Optional[int] = None


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------
@router.get("/bots/{slug}/dashboard/stats", response_model=BotDashboardStats)
def bot_dashboard_stats(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    return BotDashboardStats(
        bot_id=bot.id,
        slug=bot.slug,
        name=bot.name,
        end_user_count=EMLYUsers.count(bot.id),
        message_count=EMLYMessages.count_all(bot.id),
        file_count=_file_count(bot.id),
        member_count=len(AdminBotMemberships.list_for_bot(bot.id)),
    )


def _file_count(bot_id: str) -> int:
    from models.emly_files import EMLYFiles
    return EMLYFiles.select().where(EMLYFiles.bot == bot_id).count()


def _resolve_window(from_ts: Optional[int], to_ts: Optional[int]) -> tuple[datetime, datetime]:
    """Translate the ``from`` / ``to`` query params (unix epoch seconds) to a
    validated ``(start, end)`` pair. Defaults to the trailing 30 days. Spans
    over 366 days raise 400 — every analytics endpoint accepts the same
    cap so a malformed query can't fan out unbounded scans.

    Timestamps are interpreted as UTC seconds (the UI sends
    ``Math.floor(Date.now() / 1000)``) and returned as naive UTC datetimes
    so they compare cleanly against ``EMLYMessage.created_on`` /
    ``EMLYSession.started_at`` (both written via ``datetime.utcnow()``).
    """
    from datetime import timedelta

    end = datetime.utcfromtimestamp(to_ts) if to_ts else datetime.utcnow()
    start = datetime.utcfromtimestamp(from_ts) if from_ts else (end - timedelta(days=30))
    if end < start:
        raise HTTPException(status_code=400, detail="`to` must be on or after `from`")
    span_days = (end.date() - start.date()).days + 1
    if span_days > 366:
        raise HTTPException(status_code=400, detail="Window too wide (max 366 days)")
    return start, end


# ---------------------------------------------------------------------------
# Aggregated metric report (per-bot Analytics page)
# ---------------------------------------------------------------------------
class BotReport(BaseModel):
    """Mirrors what `EMLYMessages.get_report` returns. Numeric fields stay
    `Optional` where the source can yield `None` so the UI can render `—`
    rather than fabricating a 0% reading on zero observations."""

    actions: int
    form_submission_timestamps: List[Any] = Field(default_factory=list)
    users: int
    conversations: int
    engagement: float
    impressions: int
    short_impressions: int
    conversion_rate: float
    messages: int
    average_message_per_conversation: float
    average_conversations_per_user: float
    deflection_rate: Optional[float] = None
    deflection_count: int = 0
    csat_avg: Optional[float] = None
    csat_count: int = 0
    p95_latency_ms: Optional[int] = None
    resolution_rate: Optional[float] = None


class MessageUsageByModel(BaseModel):
    """Per-`model_used` token tally over a window. Pricing lives client-side
    (see ``ModelUsageCard``'s ``PRICE_TABLE_PER_1K``); the backend just
    returns observable counts. Models with no `model_used` (legacy rows
    from before Phase 2) are dropped — counting them under "unknown"
    would invent attribution we don't have."""

    model: str
    turns: int
    prompt_tokens: int
    completion_tokens: int


@router.get(
    "/bots/{slug}/messages/by-model",
    response_model=List[MessageUsageByModel],
)
def bot_messages_by_model(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    channel_id: Optional[str] = Query(
        None,
        description="Restrict to a single `bot_channel.id`. Omit for all channels.",
    ),
    admin: AdminUserModel = Depends(get_admin),
):
    """Aggregate assistant turns by `model_used` over a time window.

    Used by the analytics page's LLM-spend KPI (which needs an honest
    prior-period total in compare mode) and by the model-usage table
    card. Replaces a client-side iteration over the over-fetched
    message list, which silently undercounts at high volume.
    """
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    start, end = _resolve_window(from_ts, to_ts)

    from peewee import fn

    from models.emly_messages import EMLYMessage

    where = (
        (EMLYMessage.bot == bot.id)
        & (EMLYMessage.role == "assistant")
        & (EMLYMessage.created_on >= start)
        & (EMLYMessage.created_on <= end)
        & (EMLYMessage.model_used.is_null(False))
    )
    if channel_id:
        where = where & (EMLYMessage.channel_id == channel_id)
    rows = (
        EMLYMessage.select(
            EMLYMessage.model_used,
            fn.COUNT(EMLYMessage.id).alias("turns"),
            fn.COALESCE(fn.SUM(EMLYMessage.prompt_tokens), 0).alias("prompt_tokens"),
            fn.COALESCE(fn.SUM(EMLYMessage.completion_tokens), 0).alias("completion_tokens"),
        )
        .where(where)
        .group_by(EMLYMessage.model_used)
    )

    return [
        MessageUsageByModel(
            model=r["model_used"],
            turns=int(r["turns"] or 0),
            prompt_tokens=int(r["prompt_tokens"] or 0),
            completion_tokens=int(r["completion_tokens"] or 0),
        )
        for r in rows.dicts()
    ]


class FunnelResponse(BaseModel):
    """Cohort funnel for sessions started in the requested window. Each
    later step is a subset of `started`: a session counts as
    `understood` if at least one of its user messages has a non-null /
    non-empty `topic`, and as `resolved` if `is_resolved` is true at
    read time (regardless of when the resolution happened).
    `*_rate` fields are pre-computed so the UI doesn't repeat the
    division and risk drift between cards."""

    started: int
    understood: int
    resolved: int
    understood_rate: Optional[float] = None
    resolved_rate: Optional[float] = None


@router.get("/bots/{slug}/funnel", response_model=FunnelResponse)
def bot_funnel(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    channel_id: Optional[str] = Query(None),
    admin: AdminUserModel = Depends(get_admin),
):
    """Started → understood → resolved funnel for sessions in a window."""
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    start, end = _resolve_window(from_ts, to_ts)

    from peewee import fn

    from models.emly_messages import EMLYMessage
    from models.emly_sessions import EMLYSession

    in_window = (
        (EMLYSession.bot_id == bot.id)
        & (EMLYSession.started_at >= start)
        & (EMLYSession.started_at <= end)
    )
    if channel_id:
        in_window = in_window & (EMLYSession.channel_id == channel_id)

    started = EMLYSession.select().where(in_window).count()

    # Sessions in the cohort with at least one user message that the
    # intent router classified. Distinct on session_id to dedupe multi-
    # message sessions. The bot filter on EMLYMessage protects against
    # cross-tenant leakage if session_id ever collides between bots.
    understood_q = (
        EMLYSession.select(EMLYSession.id)
        .join(EMLYMessage, on=(EMLYMessage.session_id == EMLYSession.id))
        .where(
            in_window
            & (EMLYMessage.bot == bot.id)
            & (EMLYMessage.role == "user")
            & (EMLYMessage.topic.is_null(False))
            & (fn.TRIM(EMLYMessage.topic) != "")
        )
        .distinct()
    )
    understood = understood_q.count()

    resolved = (
        EMLYSession.select().where(in_window & (EMLYSession.is_resolved == True)).count()  # noqa: E712
    )

    return FunnelResponse(
        started=started,
        understood=understood,
        resolved=resolved,
        understood_rate=round(understood / started, 4) if started > 0 else None,
        resolved_rate=round(resolved / started, 4) if started > 0 else None,
    )


class MessageCountByTopic(BaseModel):
    """Per-`topic` tally over user-role messages in a window. Messages
    whose `topic` column is null or empty surface as the empty string;
    the UI renders that as an `[unclassified]` bucket so the share
    that the runtime didn't categorise stays visible."""

    topic: str
    count: int


@router.get(
    "/bots/{slug}/messages/by-topic",
    response_model=List[MessageCountByTopic],
)
def bot_messages_by_topic(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    channel_id: Optional[str] = Query(None),
    admin: AdminUserModel = Depends(get_admin),
):
    """Aggregate user turns by `topic` over a time window.

    Used by the analytics page's Top intents card so the count is exact
    rather than a sample of the recently-loaded message list. Null /
    empty topics collapse into one bucket to avoid double-counting
    when the runtime stores both forms.
    """
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    start, end = _resolve_window(from_ts, to_ts)

    from peewee import fn

    from models.emly_messages import EMLYMessage

    where = (
        (EMLYMessage.bot == bot.id)
        & (EMLYMessage.role == "user")
        & (EMLYMessage.created_on >= start)
        & (EMLYMessage.created_on <= end)
    )
    if channel_id:
        where = where & (EMLYMessage.channel_id == channel_id)
    rows = (
        EMLYMessage.select(
            EMLYMessage.topic,
            fn.COUNT(EMLYMessage.id).alias("count"),
        )
        .where(where)
        .group_by(EMLYMessage.topic)
    )

    # Collapse null and empty `topic` into one bucket — the runtime can
    # write either depending on the codepath, and counting them apart
    # would mislead the UI's Top intents tally.
    counts: dict[str, int] = {}
    for row in rows.dicts():
        key = (row["topic"] or "").strip()
        counts[key] = counts.get(key, 0) + int(row["count"] or 0)
    return [MessageCountByTopic(topic=k, count=v) for k, v in counts.items()]


class DailyMessageBucket(BaseModel):
    """One row per UTC day in the requested window. ``count`` is the
    total of persisted messages on the day; ``user_count`` /
    ``assistant_count`` split the same total by ``role`` so the UI can
    plot a stacked or overlaid view. Days with no activity are
    zero-filled."""

    day: str  # ISO YYYY-MM-DD
    count: int
    user_count: int = 0
    assistant_count: int = 0


@router.get("/bots/{slug}/messages/daily", response_model=List[DailyMessageBucket])
def bot_messages_daily(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    channel_id: Optional[str] = Query(None),
    admin: AdminUserModel = Depends(get_admin),
):
    """Per-day message counts for the volume chart.

    Returns a continuous (zero-filled) series so the UI can render
    directly. The total is split by ``role`` so the caller can render
    user / assistant overlays without a second fetch.
    """
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    start, end = _resolve_window(from_ts, to_ts)
    from datetime import timedelta

    from models.emly_messages import EMLYMessage

    where = (
        (EMLYMessage.bot == bot.id)
        & (EMLYMessage.created_on >= start)
        & (EMLYMessage.created_on <= end)
    )
    if channel_id:
        where = where & (EMLYMessage.channel_id == channel_id)

    user_counts: dict[str, int] = {}
    asst_counts: dict[str, int] = {}
    q = EMLYMessage.select(EMLYMessage.created_on, EMLYMessage.role).where(where)
    for row in q.dicts():
        ts = row["created_on"]
        if ts is None:
            continue
        key = ts.date().isoformat()
        if row["role"] == "user":
            user_counts[key] = user_counts.get(key, 0) + 1
        elif row["role"] == "assistant":
            asst_counts[key] = asst_counts.get(key, 0) + 1

    out: List[DailyMessageBucket] = []
    cursor = start.date()
    end_date = end.date()
    while cursor <= end_date:
        key = cursor.isoformat()
        u = user_counts.get(key, 0)
        a = asst_counts.get(key, 0)
        out.append(DailyMessageBucket(day=key, count=u + a, user_count=u, assistant_count=a))
        cursor += timedelta(days=1)
    return out


@router.get("/bots/{slug}/report", response_model=BotReport)
def bot_get_report(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    channel_id: Optional[str] = Query(None),
    admin: AdminUserModel = Depends(get_admin),
):
    """Bot-scoped wrapper over ``EMLYMessages.get_report``.

    Enforces membership on the slug rather than trusting a raw bot_id
    query param, and returns a typed model. Channel filter restricts
    aggregations to messages and sessions on a single ``bot_channel.id``.
    """
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    # Naive-UTC datetimes — see ``_resolve_window`` for the invariant.
    From = datetime.utcfromtimestamp(from_ts) if from_ts else None
    To = datetime.utcfromtimestamp(to_ts) if to_ts else None
    raw = EMLYMessages.get_report(bot.id, From, To, channel_id=channel_id) or {}
    return BotReport(**raw)


# ---------------------------------------------------------------------------
# Channel-mix breakdown
# ---------------------------------------------------------------------------
class MessageCountByChannel(BaseModel):
    """Per-channel message tally over a window. ``channel_id`` is null
    for legacy rows that pre-date Phase 3 channel threading; the UI
    surfaces those as `[unattributed]` so the share doesn't get folded
    into whichever channel happens to come first alphabetically."""

    channel_id: Optional[str] = None
    channel_type: Optional[str] = None
    display_name: Optional[str] = None
    count: int


@router.get(
    "/bots/{slug}/messages/by-channel",
    response_model=List[MessageCountByChannel],
)
def bot_messages_by_channel(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    admin: AdminUserModel = Depends(get_admin),
):
    """Aggregate messages by `channel_id` over a window. Powers the
    Channel-mix card and the channel filter dropdown."""
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    start, end = _resolve_window(from_ts, to_ts)

    from peewee import fn

    from models.bot_channels import BotChannel
    from models.emly_messages import EMLYMessage

    # Group counts by channel_id (including the null bucket).
    rows = (
        EMLYMessage.select(
            EMLYMessage.channel_id,
            fn.COUNT(EMLYMessage.id).alias("count"),
        )
        .where(
            (EMLYMessage.bot == bot.id)
            & (EMLYMessage.created_on >= start)
            & (EMLYMessage.created_on <= end)
        )
        .group_by(EMLYMessage.channel_id)
    )

    counts = [(r["channel_id"], int(r["count"] or 0)) for r in rows.dicts()]

    # Look up channel metadata in one shot — avoids N+1 over the bucket
    # list and keeps the response shape stable when a channel has been
    # deleted (we still report `count` under the orphaned id).
    # Defence-in-depth: scope the lookup to this bot. The channel ids
    # themselves come from messages already filtered to the tenant, but
    # if a stray row carried a re-pointed channel id we'd otherwise leak
    # the other bot's display name and type.
    ids = [c[0] for c in counts if c[0]]
    meta: dict[str, BotChannel] = {}
    if ids:
        for ch in BotChannel.select().where(
            (BotChannel.id.in_(ids)) & (BotChannel.bot_id == bot.id)
        ):
            meta[ch.id] = ch

    out: List[MessageCountByChannel] = []
    for channel_id, count in counts:
        ch = meta.get(channel_id) if channel_id else None
        out.append(
            MessageCountByChannel(
                channel_id=channel_id,
                channel_type=ch.type if ch else None,
                display_name=ch.display_name if ch else None,
                count=count,
            )
        )
    out.sort(key=lambda r: r.count, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Enrichment summary (sentiment + intent distribution from EMLYSession)
# ---------------------------------------------------------------------------
class SentimentBreakdown(BaseModel):
    positive: int = 0
    neutral: int = 0
    negative: int = 0
    unrated: int = 0


class IntentCount(BaseModel):
    intent: str
    count: int


class EnrichmentSummary(BaseModel):
    cohort_size: int  # sessions started in window
    enriched_count: int  # sessions with non-null `enrichment_at`
    sentiment: SentimentBreakdown = SentimentBreakdown()
    intents: List[IntentCount] = Field(default_factory=list)


@router.get("/bots/{slug}/sessions/enrichment-summary", response_model=EnrichmentSummary)
def bot_enrichment_summary(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    channel_id: Optional[str] = Query(None),
    admin: AdminUserModel = Depends(get_admin),
):
    """Sentiment / intent distribution from Phase 8 enrichment.

    ``cohort_size`` is the total sessions started in the window;
    ``enriched_count`` is the subset that ran through the enrichment
    worker. When the bot hasn't opted into enrichment, both numbers
    can disagree (cohort > 0, enriched == 0) and the UI calls that
    out rather than rendering 100% unrated.
    """
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    start, end = _resolve_window(from_ts, to_ts)

    from peewee import fn

    from models.emly_sessions import EMLYSession

    in_window = (
        (EMLYSession.bot_id == bot.id)
        & (EMLYSession.started_at >= start)
        & (EMLYSession.started_at <= end)
    )
    if channel_id:
        in_window = in_window & (EMLYSession.channel_id == channel_id)

    cohort_size = EMLYSession.select().where(in_window).count()
    enriched_count = (
        EMLYSession.select()
        .where(in_window & (EMLYSession.enrichment_at.is_null(False)))
        .count()
    )

    sentiment = SentimentBreakdown()
    sent_rows = (
        EMLYSession.select(
            EMLYSession.sentiment_label,
            fn.COUNT(EMLYSession.id).alias("count"),
        )
        .where(in_window)
        .group_by(EMLYSession.sentiment_label)
    )
    for r in sent_rows.dicts():
        label = (r["sentiment_label"] or "").lower()
        count = int(r["count"] or 0)
        if label == "positive":
            sentiment.positive += count
        elif label == "negative":
            sentiment.negative += count
        elif label == "neutral":
            sentiment.neutral += count
        else:
            sentiment.unrated += count

    intent_rows = (
        EMLYSession.select(
            EMLYSession.intent,
            fn.COUNT(EMLYSession.id).alias("count"),
        )
        .where(in_window & (EMLYSession.intent.is_null(False)))
        .group_by(EMLYSession.intent)
        .order_by(fn.COUNT(EMLYSession.id).desc())
        .limit(12)
    )
    intents = [
        IntentCount(intent=str(r["intent"] or ""), count=int(r["count"] or 0))
        for r in intent_rows.dicts()
        if (r["intent"] or "").strip()
    ]

    return EnrichmentSummary(
        cohort_size=cohort_size,
        enriched_count=enriched_count,
        sentiment=sentiment,
        intents=intents,
    )


# ---------------------------------------------------------------------------
# Citation statistics
# ---------------------------------------------------------------------------
class TopCitedFile(BaseModel):
    file_id: Optional[str] = None
    filename: Optional[str] = None
    citation_count: int


class CitationStats(BaseModel):
    assistant_turns: int
    with_citations: int
    citation_rate: Optional[float] = None
    top_files: List[TopCitedFile] = Field(default_factory=list)


@router.get("/bots/{slug}/messages/citation-stats", response_model=CitationStats)
def bot_citation_stats(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    channel_id: Optional[str] = Query(None),
    admin: AdminUserModel = Depends(get_admin),
):
    """Share of assistant turns that quoted a RAG citation, plus the
    top cited files. The ``citations`` column is JSON text — parsed
    here rather than at write time so the legacy storage format
    stays portable. Bots that never call RAG return ``citation_rate``
    null (no `citations` column ever populated)."""
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    start, end = _resolve_window(from_ts, to_ts)

    from models.emly_messages import EMLYMessage

    where = (
        (EMLYMessage.bot == bot.id)
        & (EMLYMessage.role == "assistant")
        & (EMLYMessage.created_on >= start)
        & (EMLYMessage.created_on <= end)
    )
    if channel_id:
        where = where & (EMLYMessage.channel_id == channel_id)

    assistant_turns = EMLYMessage.select().where(where).count()
    with_citations = 0
    file_counts: dict[str, int] = {}
    file_names: dict[str, str] = {}

    rows = EMLYMessage.select(EMLYMessage.citations).where(
        where & (EMLYMessage.citations.is_null(False))
    )
    for row in rows.dicts():
        raw = row["citations"]
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if not isinstance(parsed, list) or not parsed:
            continue
        with_citations += 1
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            file_id = entry.get("file_id") or entry.get("source") or entry.get("filename")
            if not file_id:
                continue
            file_id = str(file_id)
            file_counts[file_id] = file_counts.get(file_id, 0) + 1
            name = entry.get("filename") or entry.get("source_url")
            if name and file_id not in file_names:
                file_names[file_id] = str(name)

    top_files = [
        TopCitedFile(
            file_id=fid,
            filename=file_names.get(fid),
            citation_count=count,
        )
        for fid, count in sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    ]

    citation_rate = (
        round(with_citations / assistant_turns, 4) if assistant_turns > 0 else None
    )

    return CitationStats(
        assistant_turns=assistant_turns,
        with_citations=with_citations,
        citation_rate=citation_rate,
        top_files=top_files,
    )


# ---------------------------------------------------------------------------
# Latency quantiles (p50 / p95 / p99 of `response_time_ms`)
# ---------------------------------------------------------------------------
class LatencyQuantiles(BaseModel):
    count: int
    p50: Optional[int] = None
    p95: Optional[int] = None
    p99: Optional[int] = None


@router.get("/bots/{slug}/messages/latency-quantiles", response_model=LatencyQuantiles)
def bot_latency_quantiles(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    channel_id: Optional[str] = Query(None),
    admin: AdminUserModel = Depends(get_admin),
):
    """Compute p50/p95/p99 of assistant response_time_ms in window.

    SQLite has no PERCENTILE_CONT, and we want a single shape that
    works for both Postgres and SQLite — so the route pulls the
    latency column and computes nearest-rank percentiles in Python.
    Capped to 100k rows; beyond that we'd want a histogram bucketing
    in SQL, but the analytics page is not on that path today.
    """
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    start, end = _resolve_window(from_ts, to_ts)

    from models.emly_messages import EMLYMessage

    where = (
        (EMLYMessage.bot == bot.id)
        & (EMLYMessage.role == "assistant")
        & (EMLYMessage.created_on >= start)
        & (EMLYMessage.created_on <= end)
        & (EMLYMessage.response_time_ms.is_null(False))
    )
    if channel_id:
        where = where & (EMLYMessage.channel_id == channel_id)

    rows = (
        EMLYMessage.select(EMLYMessage.response_time_ms)
        .where(where)
        .order_by(EMLYMessage.response_time_ms.asc())
        .limit(100000)
        .dicts()
    )
    xs = [int(r["response_time_ms"]) for r in rows if r["response_time_ms"] is not None]
    if not xs:
        return LatencyQuantiles(count=0)

    def quantile(arr: List[int], q: float) -> int:
        # Nearest-rank percentile; matches `EMLYMessages.get_report`'s
        # p95 calc so the analytics page's KPI and this card agree.
        idx = max(0, int(round(q * len(arr))) - 1)
        return arr[idx]

    return LatencyQuantiles(
        count=len(xs),
        p50=quantile(xs, 0.5),
        p95=quantile(xs, 0.95),
        p99=quantile(xs, 0.99),
    )


# ---------------------------------------------------------------------------
# Hour-of-day × day-of-week heatmap
# ---------------------------------------------------------------------------
class HeatmapCell(BaseModel):
    """Mon=0 .. Sun=6 (Python's `weekday()`); ``hour`` is 0..23 UTC."""

    day_of_week: int
    hour: int
    count: int


@router.get("/bots/{slug}/messages/heatmap", response_model=List[HeatmapCell])
def bot_messages_heatmap(
    slug: str,
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
    channel_id: Optional[str] = Query(None),
    admin: AdminUserModel = Depends(get_admin),
):
    """7×24 heatmap of message volume by UTC weekday and hour.

    Returns the dense 168-cell grid (zero-filled) so the UI can render
    without filling gaps. Both roles count toward the same cell — the
    surface is interested in load patterns, not who's talking.
    """
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    start, end = _resolve_window(from_ts, to_ts)

    from models.emly_messages import EMLYMessage

    where = (
        (EMLYMessage.bot == bot.id)
        & (EMLYMessage.created_on >= start)
        & (EMLYMessage.created_on <= end)
    )
    if channel_id:
        where = where & (EMLYMessage.channel_id == channel_id)

    grid = [[0] * 24 for _ in range(7)]
    rows = EMLYMessage.select(EMLYMessage.created_on).where(where)
    for r in rows.dicts():
        ts = r["created_on"]
        if ts is None:
            continue
        grid[ts.weekday()][ts.hour] += 1

    out: List[HeatmapCell] = []
    for dow in range(7):
        for hour in range(24):
            out.append(HeatmapCell(day_of_week=dow, hour=hour, count=grid[dow][hour]))
    return out


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
@router.get("/bots/{slug}/messages", response_model=MessageListResponse)
def bot_list_messages(
    slug: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    return MessageListResponse(
        items=EMLYMessages.list_all(bot.id, user_id=user_id, session_id=session_id, skip=skip, limit=limit),
        total=EMLYMessages.count_all(bot.id, user_id=user_id, session_id=session_id),
        skip=skip,
        limit=limit,
    )


@router.get("/bots/{slug}/messages/{message_id}", response_model=EMLYMessageModel)
def bot_get_message(
    slug: str,
    message_id: int,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    msg = EMLYMessages.get_message_by_id(bot.id, message_id)
    if msg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return msg


@router.patch("/bots/{slug}/messages/{message_id}", response_model=EMLYMessageModel)
def bot_update_message(
    slug: str,
    message_id: int,
    payload: UpdateMessageRequest,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    if payload.not_useful is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update")
    updated = EMLYMessages.update_emly_message_by_id(bot.id, message_id, payload.not_useful)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return updated


@router.delete("/bots/{slug}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def bot_delete_message(
    slug: str,
    message_id: int,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    if not EMLYMessages.delete_message_by_id(bot.id, message_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")


# Phase 6 backend-backfill: admin override for the heuristic deflection
# flag. Always tags `deflection_method='admin'` so analytics can tell
# operator-labelled rows apart from heuristic ones.
class DeflectionPayload(BaseModel):
    is_deflected: Optional[bool] = None  # null clears the flag


@router.post("/bots/{slug}/messages/{message_id}/deflection", response_model=EMLYMessageModel)
def bot_set_deflection(
    slug: str,
    message_id: int,
    payload: DeflectionPayload,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    if not EMLYMessages.set_deflection(
        bot.id,
        message_id,
        is_deflected=payload.is_deflected,
        method="admin",
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    from services.audit import audit
    audit(
        action="message.deflection.override",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="message",
        target_id=str(message_id),
        payload={"is_deflected": payload.is_deflected},
    )
    updated = EMLYMessages.get_message_by_id(bot.id, message_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Message vanished after update")
    return updated


# Phase 7 backend-backfill: admin override for end-user CSAT.
class RatingPayload(BaseModel):
    rating: int = 0  # -1 = thumbs down, 0 = clear, +1 = thumbs up


@router.post("/bots/{slug}/messages/{message_id}/rating", response_model=EMLYMessageModel)
def bot_admin_set_rating(
    slug: str,
    message_id: int,
    payload: RatingPayload,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    if payload.rating not in (-1, 0, 1):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="rating must be -1, 0, or 1")
    if not EMLYMessages.set_rating(bot.id, message_id, rating=payload.rating):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    from services.audit import audit
    audit(
        action="message.rating.admin_override",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="message",
        target_id=str(message_id),
        payload={"rating": payload.rating},
    )
    updated = EMLYMessages.get_message_by_id(bot.id, message_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Message vanished after update")
    return updated


# ---------------------------------------------------------------------------
# End users
# ---------------------------------------------------------------------------
@router.get("/bots/{slug}/end-users", response_model=UserListResponse)
def bot_list_end_users(
    slug: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    return UserListResponse(
        items=EMLYUsers.get_users(bot.id, skip=skip, limit=limit),
        total=EMLYUsers.count(bot.id),
        skip=skip,
        limit=limit,
    )


@router.get("/bots/{slug}/end-users/{user_id}", response_model=EMLYUserModel)
def bot_get_end_user(
    slug: str,
    user_id: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    u = EMLYUsers.get_user_by_id(bot.id, user_id)
    if u is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return u


# ---------------------------------------------------------------------------
# Config (per-bot)
# ---------------------------------------------------------------------------
class ConfigResponse(BaseModel):
    config: Dict[str, Any]
    config_version: int


@router.get("/bots/{slug}/config", response_model=ConfigResponse)
def bot_get_config(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    cfg = get_config_for_bot(bot.id)
    return ConfigResponse(config=cfg.model_dump(mode="json"), config_version=bot.config_version)


@router.put("/bots/{slug}/config", response_model=ConfigResponse)
def bot_put_config(
    slug: str,
    payload: ConfigPayload,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)

    # Optimistic-concurrency check (Prerequisite P5 contract).
    if payload.expected_version is not None and payload.expected_version != bot.config_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "config_version mismatch",
                "current_version": bot.config_version,
                "expected_version": payload.expected_version,
            },
        )

    save_config_for_bot(bot.id, payload.config)
    refreshed = Bots.get_by_id(bot.id)
    # Rebuild the per-bot runtime so the next chat picks up the new
    # config / topics / api_key.
    try:
        from utils.dependencies import AGENT_SERVICE_INSTANCE
        AGENT_SERVICE_INSTANCE.invalidate_bot(bot.id)
    except Exception:
        log.exception("invalidate_bot failed after config save bot=%s", bot.id)

    cfg = get_config_for_bot(bot.id)
    return ConfigResponse(
        config=cfg.model_dump(mode="json"),
        config_version=refreshed.config_version if refreshed else bot.config_version,
    )


# ---------------------------------------------------------------------------
# LLM api key (write-only — never returned in responses; encrypted at rest
# via services.bot_config.set_api_key -> services.secrets.encrypt).
# ---------------------------------------------------------------------------
class ApiKeyPayload(BaseModel):
    api_key: Optional[str] = None  # ``None`` clears the stored key.


@router.put("/bots/{slug}/api-key", status_code=status.HTTP_204_NO_CONTENT)
def bot_put_api_key(
    slug: str,
    payload: ApiKeyPayload,
    admin: AdminUserModel = Depends(get_admin),
):
    bot = _resolve_bot(slug)
    _require_writer(admin.id, bot.id)
    set_api_key(bot.id, payload.api_key)
    # Force the runtime to pick up the new key on the next chat.
    try:
        from utils.dependencies import AGENT_SERVICE_INSTANCE
        AGENT_SERVICE_INSTANCE.invalidate_bot(bot.id)
    except Exception:
        log.exception("invalidate_bot failed after api-key change bot=%s", bot.id)


@router.get("/bots/{slug}/api-key/status")
def bot_get_api_key_status(
    slug: str,
    admin: AdminUserModel = Depends(get_admin),
):
    """Return whether the bot has an api_key set, without leaking its
    value. The structured config editor uses this to render
    'Key configured' vs 'No key set'."""
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)
    return {"has_key": bool(bot.api_key_encrypted)}


# ---------------------------------------------------------------------------
# RAG search inspector — admin-only "what does the bot see?" tool.
# ---------------------------------------------------------------------------
class RAGSearchRequest(BaseModel):
    """Bounds mirror the UI's input clamps so a malformed (or hostile)
    request can't drive a runaway Qdrant scan or empty-query call."""

    query: str = Field(..., min_length=1, max_length=2000)
    top_k: Optional[int] = Field(None, ge=1, le=50)
    threshold: Optional[float] = Field(None, ge=0.0, le=1.0)


class RAGSearchHit(BaseModel):
    score: Optional[float] = None
    chunk: str
    metadata: Dict[str, Any]


class RAGSearchResponse(BaseModel):
    query: str
    top_k: int
    threshold: float
    hits: List[RAGSearchHit]


@router.post("/bots/{slug}/rag/search", response_model=RAGSearchResponse)
def bot_rag_search(
    slug: str,
    payload: RAGSearchRequest,
    admin: AdminUserModel = Depends(get_admin),
):
    """Run the same retrieval the chat runtime would, but return the
    raw hits — score, source filename, chunk text — so admins can
    debug why their bot is answering (or not answering) a query."""
    bot = _resolve_bot(slug)
    _require_member(admin.id, bot.id)

    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Query is required")

    from agents.rag_manager import get_rag_manager
    cfg = get_config_for_bot(bot.id)
    rag_cfg = cfg.rag if cfg else None
    top_k = payload.top_k if payload.top_k is not None else (rag_cfg.top_k if rag_cfg else 5)
    threshold = (
        payload.threshold
        if payload.threshold is not None
        else (rag_cfg.embedding_threshold if rag_cfg else 0.20)
    )

    _, citations = get_rag_manager().search(
        bot_id=bot.id,
        query=query,
        top_k=top_k,
        embedding_threshold=threshold,
    )
    hits = [
        RAGSearchHit(
            score=(c.get("metadata") or {}).get("relevance_score"),
            chunk=c.get("chunk", ""),
            metadata=c.get("metadata") or {},
        )
        for c in citations
    ]
    return RAGSearchResponse(query=query, top_k=top_k, threshold=threshold, hits=hits)
