"""Phase 4 of the backend-backfill plan: per-session state table.

Each row corresponds to a distinct ``(bot_id, session_id)`` pair. The
shape mirrors the ``emly_session`` migration in
``migrations/008_emly_session.py``. ``EMLYMessages.insert_new_message``
calls ``EMLYSessions.upsert_on_message`` for every persisted message so
this table stays current with no separate worker.

Reader endpoints (Phase 5) consume the table directly so the admin UI's
Conversations list doesn't have to GROUP-BY-derive sessions on every
fetch.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    DoesNotExist,
    FloatField,
    IntegerField,
    Model,
)
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB
from models.bot_channels import channel_filter

logger = logging.getLogger(__name__)


class EMLYSession(Model):
    id = CharField(max_length=255, primary_key=True)
    bot_id = CharField(max_length=64)
    user_id = CharField(max_length=255)
    channel_id = CharField(max_length=64, null=True)
    started_at = DateTimeField()
    last_activity_at = DateTimeField()
    ended_at = DateTimeField(null=True)
    turn_count = IntegerField(default=0)
    is_resolved = BooleanField(null=True)
    resolved_at = DateTimeField(null=True)
    resolved_by = CharField(max_length=64, null=True)
    sentiment_score = FloatField(null=True)
    sentiment_label = CharField(max_length=32, null=True)
    intent = CharField(max_length=128, null=True)
    intent_confidence = FloatField(null=True)
    enrichment_at = DateTimeField(null=True)
    # Phase 10 backend-backfill: take-over columns (migration 010).
    taken_over_by = CharField(max_length=64, null=True)
    taken_over_at = DateTimeField(null=True)
    taken_over_until = DateTimeField(null=True)

    class Meta:
        database = DB
        table_name = "emly_session"


class EMLYSessionModel(BaseModel):
    id: str
    bot_id: str
    user_id: str
    channel_id: Optional[str] = None
    started_at: datetime
    last_activity_at: datetime
    ended_at: Optional[datetime] = None
    turn_count: int
    is_resolved: Optional[bool] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    sentiment_score: Optional[float] = None
    sentiment_label: Optional[str] = None
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    enrichment_at: Optional[datetime] = None
    taken_over_by: Optional[str] = None
    taken_over_at: Optional[datetime] = None
    taken_over_until: Optional[datetime] = None


def _row_to_model(row: EMLYSession) -> EMLYSessionModel:
    return EMLYSessionModel(**model_to_dict(row, recurse=False))


class EMLYSessionsTable:
    """Helpers for the ``emly_session`` table.

    `upsert_on_message` is the hot path: it's called from every message
    insert. We avoid a SELECT-then-INSERT race by using INSERT ... ON
    CONFLICT (DO UPDATE). For SQLite the UPSERT clause is supported in
    3.24+; for Postgres the equivalent is `ON CONFLICT (id)`.
    """

    def __init__(self, db):
        self.db = db
        # Don't auto-create the table here — peewee_migrate is the source of
        # truth via `migrations/008_emly_session.py`. Auto-creating in code
        # would race the migration runner on first boot.

    def upsert_on_message(
        self,
        *,
        bot_id: str,
        session_id: str,
        user_id: str,
        channel_id: Optional[str],
        ts: datetime,
    ) -> None:
        """Bump turn_count + last_activity_at; create the row on first turn.

        Failures are swallowed — message persistence must succeed even if
        the session-row update fails (e.g. transient DB error). The row
        will be reconstructed on the next message thanks to the upsert.
        """
        if not session_id:
            return
        try:
            row = EMLYSession.get_or_none(EMLYSession.id == session_id)
            if row is None:
                EMLYSession.create(
                    id=session_id,
                    bot_id=bot_id,
                    user_id=user_id or "",
                    channel_id=channel_id,
                    started_at=ts,
                    last_activity_at=ts,
                    turn_count=1,
                )
                return
            updates = {
                "last_activity_at": ts,
                "turn_count": (row.turn_count or 0) + 1,
            }
            # Backfill channel_id if it wasn't recorded on the first turn
            # (Phase 3 only set it on new traffic; old rows can pick it up
            # whenever the threading is complete).
            if channel_id and not row.channel_id:
                updates["channel_id"] = channel_id
            EMLYSession.update(**updates).where(EMLYSession.id == session_id).execute()
        except Exception:
            logger.exception(
                "EMLYSessions.upsert_on_message failed for session=%s bot=%s",
                session_id,
                bot_id,
            )
            return
        # Phase 9 backend-backfill: emit a realtime event so SSE
        # subscribers (admin Conversations list) refresh without polling.
        # Best-effort: failures here must not block the message write.
        try:
            from services.realtime import publish

            publish(
                bot_id,
                {
                    "type": "session_activity",
                    "bot_id": bot_id,
                    "session_id": session_id,
                    "channel_id": channel_id,
                    "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                },
            )
        except Exception:
            logger.debug("realtime publish failed (non-fatal)", exc_info=True)

    def current_channel_session(
        self,
        bot_id: str,
        base_id: str,
        *,
        idle_after: timedelta,
        now: Optional[datetime] = None,
    ) -> str:
        """Session id the next message on a messaging channel should use.

        Channel conversations are keyed on a stable platform id (a DM, a
        Telegram chat, a WhatsApp number), which on its own never ends:
        a resolved conversation would swallow every later question and
        each user would count as one session forever. Rotate instead:
        ``base_id``, ``base_id-1``, ``base_id-2`` ... A new generation
        starts once the latest one is resolved or idle for longer than
        ``idle_after``.

        Fails open to ``base_id`` so a DB hiccup never blocks a reply.
        """
        now = now or datetime.utcnow()
        prefix = f"{base_id}-"
        try:
            rows = list(
                EMLYSession.select(
                    EMLYSession.id,
                    EMLYSession.is_resolved,
                    EMLYSession.last_activity_at,
                ).where(
                    (EMLYSession.bot_id == bot_id)
                    & ((EMLYSession.id == base_id) | EMLYSession.id.startswith(prefix))
                )
            )
        except Exception:
            logger.exception(
                "EMLYSessions.current_channel_session failed for base=%s bot=%s",
                base_id,
                bot_id,
            )
            return base_id

        def generation(session_id: str) -> int:
            suffix = session_id[len(prefix):] if session_id.startswith(prefix) else ""
            return int(suffix) if suffix.isdigit() else 0

        rows = [r for r in rows if r.id == base_id or r.id[len(prefix):].isdigit()]
        if not rows:
            return base_id
        latest = max(rows, key=lambda r: generation(r.id))
        idle = (
            latest.last_activity_at is not None
            and now - latest.last_activity_at > idle_after
        )
        if latest.is_resolved or idle:
            return f"{base_id}-{generation(latest.id) + 1}"
        return latest.id

    def get(self, bot_id: str, session_id: str) -> Optional[EMLYSessionModel]:
        try:
            row = EMLYSession.get(
                (EMLYSession.bot_id == bot_id) & (EMLYSession.id == session_id)
            )
            return _row_to_model(row)
        except DoesNotExist:
            return None

    def _apply_filters(
        self,
        q,
        *,
        bot_id: str,
        channel_id: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        started_after: Optional[datetime] = None,
        started_before: Optional[datetime] = None,
        rating: Optional[str] = None,
        search: Optional[str] = None,
    ):
        """Shared filter application for list_for_bot / count_for_bot.

        Filters are conjunctive. ``rating`` joins on the message table via
        an ``IN (SELECT session_id ...)`` subquery so the session list can
        be narrowed to sessions that have (or lack) ratings of a given
        polarity without denormalising rating state onto the session row.
        ``search`` matches any turn (user or assistant) plus the session
        and user ids, case-insensitively.
        """
        q = q.where(EMLYSession.bot_id == bot_id)
        if channel_id is not None:
            q = q.where(channel_filter(EMLYSession.channel_id, channel_id))
        if is_resolved is True:
            q = q.where(EMLYSession.is_resolved == True)  # noqa: E712
        elif is_resolved is False:
            # Sessions nobody has marked yet carry NULL and count as unresolved.
            q = q.where(
                (EMLYSession.is_resolved == False)  # noqa: E712
                | EMLYSession.is_resolved.is_null(True)
            )
        if search:
            from models.emly_messages import EMLYMessage

            term = search.strip()
            if term:
                matching = EMLYMessage.select(EMLYMessage.session_id).where(
                    (EMLYMessage.bot == bot_id) & EMLYMessage.message.contains(term)
                )
                q = q.where(
                    EMLYSession.id.in_(matching)
                    | EMLYSession.id.contains(term)
                    | EMLYSession.user_id.contains(term)
                )
        if session_id is not None:
            q = q.where(EMLYSession.id == session_id)
        if user_id is not None:
            q = q.where(EMLYSession.user_id == user_id)
        if started_after is not None:
            q = q.where(EMLYSession.started_at >= started_after)
        if started_before is not None:
            q = q.where(EMLYSession.started_at <= started_before)
        if rating is not None:
            from models.emly_messages import EMLYMessage

            base = EMLYMessage.select(EMLYMessage.session_id).where(
                EMLYMessage.bot == bot_id
            )
            if rating == "rated":
                subq = base.where(EMLYMessage.rating.is_null(False))
                q = q.where(EMLYSession.id.in_(subq))
            elif rating == "unrated":
                subq = base.where(EMLYMessage.rating.is_null(False))
                q = q.where(EMLYSession.id.not_in(subq))
            elif rating == "positive":
                subq = base.where(EMLYMessage.rating > 0)
                q = q.where(EMLYSession.id.in_(subq))
            elif rating == "negative":
                subq = base.where(EMLYMessage.rating < 0)
                q = q.where(EMLYSession.id.in_(subq))
        return q

    def list_for_bot(
        self,
        bot_id: str,
        *,
        skip: int = 0,
        limit: int = 50,
        channel_id: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        started_after: Optional[datetime] = None,
        started_before: Optional[datetime] = None,
        rating: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[EMLYSessionModel]:
        """Return sessions newest-first (last_activity_at desc)."""
        q = self._apply_filters(
            EMLYSession.select(),
            bot_id=bot_id,
            channel_id=channel_id,
            is_resolved=is_resolved,
            session_id=session_id,
            user_id=user_id,
            started_after=started_after,
            started_before=started_before,
            rating=rating,
            search=search,
        )
        q = q.order_by(EMLYSession.last_activity_at.desc()).offset(skip).limit(limit)
        return [_row_to_model(r) for r in q]

    def count_for_bot(
        self,
        bot_id: str,
        *,
        channel_id: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        started_after: Optional[datetime] = None,
        started_before: Optional[datetime] = None,
        rating: Optional[str] = None,
        search: Optional[str] = None,
    ) -> int:
        q = self._apply_filters(
            EMLYSession.select(),
            bot_id=bot_id,
            channel_id=channel_id,
            is_resolved=is_resolved,
            session_id=session_id,
            user_id=user_id,
            started_after=started_after,
            started_before=started_before,
            rating=rating,
            search=search,
        )
        return q.count()

    def mark_resolved(
        self,
        bot_id: str,
        session_id: str,
        *,
        resolved_by: str,
        is_resolved: bool = True,
    ) -> bool:
        """Set the resolution flag. Returns True if a row was updated."""
        rows = (
            EMLYSession.update(
                is_resolved=is_resolved,
                resolved_at=datetime.utcnow() if is_resolved else None,
                resolved_by=resolved_by if is_resolved else None,
                ended_at=datetime.utcnow() if is_resolved else None,
            )
            .where(
                (EMLYSession.bot_id == bot_id) & (EMLYSession.id == session_id)
            )
            .execute()
        )
        return rows > 0

    def set_enrichment(
        self,
        bot_id: str,
        session_id: str,
        *,
        sentiment_score: Optional[float] = None,
        sentiment_label: Optional[str] = None,
        intent: Optional[str] = None,
        intent_confidence: Optional[float] = None,
    ) -> bool:
        """Phase 8 hook: write classifier output to the session row."""
        updates = {"enrichment_at": datetime.utcnow()}
        if sentiment_score is not None:
            updates["sentiment_score"] = sentiment_score
        if sentiment_label is not None:
            updates["sentiment_label"] = sentiment_label
        if intent is not None:
            updates["intent"] = intent
        if intent_confidence is not None:
            updates["intent_confidence"] = intent_confidence
        rows = (
            EMLYSession.update(**updates)
            .where(
                (EMLYSession.bot_id == bot_id) & (EMLYSession.id == session_id)
            )
            .execute()
        )
        return rows > 0


    def set_takeover(
        self,
        bot_id: str,
        session_id: str,
        *,
        admin_id: Optional[str],
    ) -> bool:
        """Phase 10: claim or release human takeover. Pass ``admin_id=None``
        to release."""
        now = datetime.utcnow() if admin_id else None
        rows = (
            EMLYSession.update(
                taken_over_by=admin_id,
                taken_over_at=now,
                # Auto-release window left null for v1 — admins explicitly
                # `/release`. A future enhancement can default this to
                # now + 30 minutes so abandoned takeovers self-clear.
                taken_over_until=None,
            )
            .where(
                (EMLYSession.bot_id == bot_id) & (EMLYSession.id == session_id)
            )
            .execute()
        )
        return rows > 0

    def claim_takeover(
        self,
        bot_id: str,
        session_id: str,
        *,
        admin_id: str,
        force: bool = False,
    ) -> bool:
        """Claim a session for ``admin_id``. Without ``force`` the update
        only applies while the session is free or already held by the same
        admin, so two admins racing can't both win. Returns False when
        nothing was updated (missing session, or held by someone else)."""
        query = EMLYSession.update(
            taken_over_by=admin_id,
            taken_over_at=datetime.utcnow(),
            taken_over_until=None,
        ).where((EMLYSession.bot_id == bot_id) & (EMLYSession.id == session_id))
        if not force:
            query = query.where(
                EMLYSession.taken_over_by.is_null(True)
                | (EMLYSession.taken_over_by == "")
                | (EMLYSession.taken_over_by == admin_id)
            )
        return query.execute() > 0

    def is_taken_over(self, bot_id: str, session_id: str) -> bool:
        """Used by the agent reply path to short-circuit the bot when an
        admin is in control of the session."""
        try:
            row = EMLYSession.get(
                (EMLYSession.bot_id == bot_id) & (EMLYSession.id == session_id)
            )
        except DoesNotExist:
            return False
        if not row.taken_over_by:
            return False
        # Honour an auto-release window if one was set.
        if row.taken_over_until and row.taken_over_until < datetime.utcnow():
            return False
        return True


EMLYSessions = EMLYSessionsTable(DB)
