import logging

from pydantic import BaseModel
from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DataError,
    DateTimeField,
    DoesNotExist,
    ForeignKeyField,
    IntegerField,
    IntegrityError,
    Model,
    OperationalError,
    TextField,
    fn,
)
from playhouse.shortcuts import model_to_dict
from typing import List, Optional
from db.db import DB
from datetime import datetime

from models.bot_impressions import Bot_Impressions
from models.bots import Bot
from config import IMPRESSION_USER

####################
# Message DB Schema
####################


class EMLYMessage(Model):
    id = AutoField()
    bot = ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="messages")
    user_id = CharField(max_length=255)
    session_id = CharField(max_length=255)
    message = TextField()
    role = CharField(max_length=255)
    created_on = DateTimeField()
    updated_on = DateTimeField()
    not_useful = BooleanField(default=False)
    expanded_query = TextField(null=True)
    page = TextField(null=True)
    topic = CharField(max_length=255, null=True)

    # Phase 1 of the backend backfill (see backend-backfill.md). All
    # nullable; Phase 2 (LLM telemetry), Phase 3 (channel threading),
    # Phase 6 (deflection), and Phase 7 (CSAT) populate them.
    channel_id = CharField(max_length=64, null=True)
    model_used = CharField(max_length=128, null=True)
    prompt_tokens = IntegerField(null=True)
    completion_tokens = IntegerField(null=True)
    response_time_ms = IntegerField(null=True)
    is_deflected = BooleanField(null=True)
    deflection_method = CharField(max_length=32, null=True)
    rating = IntegerField(null=True)
    rated_at = DateTimeField(null=True)
    citations = TextField(null=True)

    class Meta:
        database = DB
        table_name = "emly_messages"


class EMLYMessageModel(BaseModel):
    id: Optional[int] = None
    bot_id: str
    user_id: str
    session_id: str
    message: str
    role: str
    created_on: datetime
    updated_on: datetime
    not_useful: bool
    expanded_query: Optional[str] = None
    page: Optional[str] = None
    topic: Optional[str] = None
    # Phase 1 telemetry / linkage / rating fields. All nullable until the
    # corresponding capture phases land. See backend-backfill.md.
    channel_id: Optional[str] = None
    model_used: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    response_time_ms: Optional[int] = None
    is_deflected: Optional[bool] = None
    deflection_method: Optional[str] = None
    rating: Optional[int] = None
    rated_at: Optional[datetime] = None
    citations: Optional[str] = None  # JSON-encoded; readers parse on demand


def _row_to_model(message: EMLYMessage) -> EMLYMessageModel:
    data = model_to_dict(message, recurse=False)
    return EMLYMessageModel(
        id=data.get("id"),
        bot_id=data["bot"],
        user_id=data["user_id"],
        session_id=data["session_id"],
        message=data["message"],
        role=data["role"],
        created_on=data["created_on"],
        updated_on=data["updated_on"],
        not_useful=data["not_useful"],
        expanded_query=data.get("expanded_query"),
        page=data.get("page"),
        topic=data.get("topic"),
        channel_id=data.get("channel_id"),
        model_used=data.get("model_used"),
        prompt_tokens=data.get("prompt_tokens"),
        completion_tokens=data.get("completion_tokens"),
        response_time_ms=data.get("response_time_ms"),
        is_deflected=data.get("is_deflected"),
        deflection_method=data.get("deflection_method"),
        rating=data.get("rating"),
        rated_at=data.get("rated_at"),
        citations=data.get("citations"),
    )


class EMLYMessageUser(Model):
    """Read-only join shape used by the CSV export — not a real table."""

    message_id = AutoField()
    bot_id = CharField()
    user_id = CharField()
    session_id = CharField()
    message = CharField()
    role = CharField()
    created_on = DateTimeField()
    not_useful = BooleanField()
    page = CharField()
    topic = CharField(null=True)
    first_name = CharField()
    last_name = CharField()
    email = CharField()
    phone = CharField()
    country = CharField()
    city = CharField()
    region = CharField()
    latitude = CharField()
    longitude = CharField()


class EMLYMessagesTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([EMLYMessage])

    def insert_new_message(
        self,
        bot_id: str,
        user_id: str,
        session_id: str,
        role: str,
        message: str,
        not_useful: bool,
        expanded_query: Optional[str],
        page: Optional[str],
        topic: Optional[str] = None,
        # Phase 1 backend-backfill: keyword-only optionals so existing
        # callers continue to work unchanged. Phase 2 (telemetry),
        # Phase 3 (channels), Phase 6 (deflection), and Phase 7 (CSAT)
        # populate these. See backend-backfill.md.
        *,
        channel_id: Optional[str] = None,
        model_used: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        response_time_ms: Optional[int] = None,
        is_deflected: Optional[bool] = None,
        deflection_method: Optional[str] = None,
        rating: Optional[int] = None,
        rated_at: Optional[datetime] = None,
        citations: Optional[str] = None,
    ) -> Optional[EMLYMessage]:
        now = datetime.utcnow()
        row = EMLYMessage.create(
            bot=bot_id,
            user_id=user_id,
            session_id=session_id,
            message=message,
            role=role,
            not_useful=not_useful,
            created_on=now,
            updated_on=now,
            expanded_query=expanded_query,
            page=page,
            topic=topic,
            channel_id=channel_id,
            model_used=model_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            response_time_ms=response_time_ms,
            is_deflected=is_deflected,
            deflection_method=deflection_method,
            rating=rating,
            rated_at=rated_at,
            citations=citations,
        )
        # Phase 4 backend-backfill: maintain the per-session row so the
        # admin Conversations endpoint can read session state in O(1).
        # Imported lazily to avoid a circular import at module load
        # (emly_sessions imports nothing from this file, but this file
        # is loaded earlier than emly_sessions during DB init).
        try:
            from models.emly_sessions import EMLYSessions
            EMLYSessions.upsert_on_message(
                bot_id=bot_id,
                session_id=session_id,
                user_id=user_id,
                channel_id=channel_id,
                ts=now,
            )
        except Exception:
            logging.exception(
                "EMLYSessions.upsert_on_message failed (non-fatal) for session=%s",
                session_id,
            )
        # Phase 8 backend-backfill: enqueue enrichment for user turns
        # only. The worker is opt-in per bot and short-circuits when
        # disabled. Failures here are silent — enrichment is best-effort.
        if role == "user" and row is not None:
            try:
                from services.enrichment import enqueue as _enqueue_enrichment
                _enqueue_enrichment(
                    bot_id=bot_id,
                    session_id=session_id,
                    message_id=row.id,
                    user_text=message,
                )
            except Exception:
                logging.debug("enrichment enqueue failed (non-fatal)", exc_info=True)
        return row

    def set_deflection(
        self,
        bot_id: str,
        message_id: int,
        *,
        is_deflected: Optional[bool],
        method: str = "admin",
    ) -> bool:
        """Phase 6 backfill: admin override for the deflection flag."""
        rows = (
            EMLYMessage.update(
                is_deflected=is_deflected,
                deflection_method=method,
                updated_on=datetime.utcnow(),
            )
            .where(
                (EMLYMessage.bot == bot_id) & (EMLYMessage.id == message_id)
            )
            .execute()
        )
        return rows > 0

    def set_rating(
        self,
        bot_id: str,
        message_id: int,
        *,
        rating: int,
    ) -> bool:
        """Phase 7 backfill: thumbs-up/down rating from end-users (or admin
        override). ``rating`` must be one of -1, 0, +1; the route layer
        validates the range."""
        rows = (
            EMLYMessage.update(
                rating=rating,
                rated_at=datetime.utcnow(),
                updated_on=datetime.utcnow(),
            )
            .where(
                (EMLYMessage.bot == bot_id) & (EMLYMessage.id == message_id)
            )
            .execute()
        )
        return rows > 0

    def get_message_by_id(self, bot_id: str, id: int) -> Optional[EMLYMessageModel]:
        try:
            row = EMLYMessage.get(
                (EMLYMessage.id == id) & (EMLYMessage.bot == bot_id)
            )
            return _row_to_model(row)
        except DoesNotExist:
            return None
        except Exception:
            logging.exception("get_message_by_id failed for id=%s bot=%s", id, bot_id)
            raise

    def get_messages(
        self,
        bot_id: str,
        user_id: str,
        session_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> List[EMLYMessageModel]:
        return [
            _row_to_model(m)
            for m in (
                EMLYMessage.select()
                .where(
                    (EMLYMessage.bot == bot_id)
                    & (EMLYMessage.user_id == user_id)
                    & (EMLYMessage.session_id == session_id)
                )
                .order_by(EMLYMessage.created_on.desc())
                .limit(limit)
                .offset(skip)
            )
            if m.expanded_query not in ("VIEW_BOT", "BOT_VIEWED")
        ]

    def list_all(
        self,
        bot_id: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[EMLYMessageModel]:
        query = EMLYMessage.select().where(EMLYMessage.bot == bot_id)
        if user_id:
            query = query.where(EMLYMessage.user_id == user_id)
        if session_id:
            query = query.where(EMLYMessage.session_id == session_id)
        query = query.order_by(EMLYMessage.created_on.desc()).offset(skip).limit(limit)
        return [_row_to_model(m) for m in query]

    def count_all(
        self,
        bot_id: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        query = EMLYMessage.select().where(EMLYMessage.bot == bot_id)
        if user_id:
            query = query.where(EMLYMessage.user_id == user_id)
        if session_id:
            query = query.where(EMLYMessage.session_id == session_id)
        return query.count()

    def get_messages_by_topic(
        self,
        bot_id: str,
        user_id: str,
        session_id: str,
        topic: str,
        skip: int = 0,
        limit: int = 50,
    ) -> List[EMLYMessageModel]:
        return [
            _row_to_model(m)
            for m in (
                EMLYMessage.select()
                .where(
                    (EMLYMessage.bot == bot_id)
                    & (EMLYMessage.user_id == user_id)
                    & (EMLYMessage.session_id == session_id)
                    & (EMLYMessage.topic == topic)
                )
                .order_by(EMLYMessage.created_on.desc())
                .limit(limit)
                .offset(skip)
            )
            if m.expanded_query
            not in ("VIEW_BOT", "BOT_VIEWED")
        ]

    def update_emly_message_by_id(
        self, bot_id: str, id: int, not_useful: bool
    ) -> Optional[EMLYMessageModel]:
        try:
            (
                EMLYMessage.update(not_useful=not_useful, updated_on=datetime.utcnow())
                .where((EMLYMessage.id == id) & (EMLYMessage.bot == bot_id))
                .execute()
            )
            return self.get_message_by_id(bot_id, id)
        except DoesNotExist:
            return None
        except (IntegrityError, DataError) as e:
            raise Exception(f"Database error during update: {str(e)}")
        except OperationalError as e:
            raise Exception(f"Operational error during update: {str(e)}")
        except Exception as e:
            raise Exception(f"An error occurred during the update: {str(e)}")

    def delete_message_by_id(self, bot_id: str, id: int) -> bool:
        try:
            rows = (
                EMLYMessage.delete()
                .where((EMLYMessage.id == id) & (EMLYMessage.bot == bot_id))
                .execute()
            )
            return rows > 0
        except (IntegrityError, DataError) as e:
            raise Exception(f"Database error during deletion: {str(e)}")
        except OperationalError as e:
            raise Exception(f"Operational error during deletion: {str(e)}")
        except Exception as e:
            raise Exception(f"An error occurred during the deletion: {str(e)}")

    def get_messages_as_csv(
        self, bot_id: str, user_id: str, session_id: Optional[str]
    ) -> List[EMLYMessageModel]:
        query = EMLYMessage.select().where(
            (EMLYMessage.bot == bot_id) & (EMLYMessage.user_id == user_id)
        )
        if session_id:
            query = query.where(EMLYMessage.session_id == session_id)
        return [_row_to_model(m) for m in query]

    def get_messages_from_to_as_csv(
        self,
        bot_id: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        from_timestamp: datetime = None,
        to_timestamp: datetime = None,
        after_message_id: int = None,
    ) -> List[EMLYMessageUser]:
        from models.emly_users import EMLYUser

        from_date = from_timestamp if from_timestamp else datetime.min
        to_date = to_timestamp if to_timestamp else datetime.max

        query = (
            EMLYMessage.select(
                EMLYMessage.id.alias("message_id"),
                EMLYMessage.bot.alias("bot_id"),
                EMLYMessage.user_id,
                EMLYMessage.session_id,
                EMLYMessage.message,
                EMLYMessage.role,
                EMLYMessage.created_on,
                EMLYMessage.not_useful,
                EMLYMessage.page,
                EMLYMessage.topic,
                EMLYUser.first_name,
                EMLYUser.last_name,
                EMLYUser.email,
                EMLYUser.phone,
                EMLYUser.country,
                EMLYUser.city,
                EMLYUser.region,
                EMLYUser.latitude,
                EMLYUser.longitude,
            )
            .join(EMLYUser, on=(EMLYUser.id == EMLYMessage.user_id))
            .where(
                (EMLYMessage.bot == bot_id)
                & (EMLYMessage.created_on >= from_date)
                & (EMLYMessage.created_on <= to_date)
            )
        )
        if session_id:
            query = query.where(EMLYMessage.session_id == session_id)
        if user_id:
            query = query.where(EMLYMessage.user_id == user_id)
        if after_message_id:
            query = query.where(EMLYMessage.id > after_message_id)

        results: List[EMLYMessageUser] = []
        for message in query.dicts():
            row = EMLYMessageUser(**message)
            if row.message not in ("VIEW_BOT", "BOT_VIEWED"):
                results.append(row)
        return results

    def get_report(
        self,
        bot_id: str,
        from_timestamp: datetime = None,
        to_timestamp: datetime = None,
        *,
        channel_id: Optional[str] = None,
    ) -> dict:
        """Aggregate KPIs for a bot over a time window.

        ``channel_id`` (Phase A of the analytics overhaul) restricts both
        the message-side and session-side queries to a single channel
        when supplied. Other callers leave it ``None`` to keep the
        bot-wide behaviour unchanged.
        """
        from_date = from_timestamp if from_timestamp else datetime.min
        to_date = to_timestamp if to_timestamp else datetime.max

        base = EMLYMessage.select().where(
            (EMLYMessage.bot == bot_id)
            & (EMLYMessage.created_on >= from_date)
            & (EMLYMessage.created_on <= to_date)
        )
        if channel_id:
            base = base.where(EMLYMessage.channel_id == channel_id)

        impressions = base.where(EMLYMessage.message == "VIEW_BOT").count()
        bot_impressions = Bot_Impressions.get_impressions(
            bot_id=bot_id, from_timestamp=from_timestamp, to_timestamp=to_timestamp
        )
        impressions += len([i for i in bot_impressions if i.impression_type == "LONG"])
        short_impressions = len([i for i in bot_impressions if i.impression_type == "SHORT"])

        leads = 0

        users_count = base.select(EMLYMessage.user_id).distinct().count()
        impression_user = (
            base.select(EMLYMessage.user_id)
            .where(EMLYMessage.user_id == IMPRESSION_USER)
            .distinct()
            .count()
        )
        conversations = base.select(EMLYMessage.session_id).distinct().count()
        messages = base.count()
        if impression_user > 0:
            users_count -= 1
            bot_messages = base.where(EMLYMessage.message == "VIEW_BOT").count()
            messages -= bot_messages * 2
            conversations -= bot_messages * 2
        users_count = users_count if users_count >= 0 else 0

        engagement = round((users_count / impressions) * 100, 2) if impressions > 0 else 0
        engagement = engagement if engagement <= 100 else 100
        conversion_rate = round((leads / users_count) * 100, 2) if users_count > 0 else 0
        conversion_rate = conversion_rate if conversion_rate <= 100 else 100

        average_message_per_conversation = (
            round(messages / conversations, 2) if conversations > 0 else 0
        )
        average_conversations_per_user = (
            round(conversations / users_count, 2) if users_count > 0 else 0
        )
        list_of_time_stamps = []

        # Phase 6 backfill: deflection rate over assistant messages with a
        # non-null `is_deflected`. Phase 7: CSAT mean over rated assistant
        # messages. Phase 2: p95 latency. All return None when there are
        # zero observations so the UI can render `—` honestly.
        assistant_base = base.where(EMLYMessage.role == "assistant")
        deflection_total = assistant_base.where(EMLYMessage.is_deflected.is_null(False)).count()
        deflection_true = assistant_base.where(EMLYMessage.is_deflected == True).count()  # noqa: E712
        deflection_rate = (
            round(deflection_true / deflection_total, 4) if deflection_total > 0 else None
        )

        rating_rows = list(
            assistant_base.where(EMLYMessage.rating.is_null(False))
            .select(EMLYMessage.rating)
            .dicts()
        )
        if rating_rows:
            ratings = [r["rating"] for r in rating_rows if r["rating"] is not None and r["rating"] != 0]
            csat_count = len(ratings)
            csat_avg = round(sum(ratings) / csat_count, 4) if csat_count else None
        else:
            csat_count = 0
            csat_avg = None

        latency_rows = list(
            assistant_base.where(EMLYMessage.response_time_ms.is_null(False))
            .select(EMLYMessage.response_time_ms)
            .dicts()
        )
        p95_latency_ms: Optional[int] = None
        if latency_rows:
            xs = sorted(int(r["response_time_ms"]) for r in latency_rows if r["response_time_ms"] is not None)
            if xs:
                # nearest-rank percentile so a single sample isn't averaged
                # with phantom zeros.
                idx = max(0, int(round(0.95 * len(xs))) - 1)
                p95_latency_ms = xs[idx]

        # Resolution rate is per-session; query EMLYSession lazily so this
        # file doesn't import the sibling at load time. Returns None when
        # the table is empty for the bot in the window.
        resolution_rate: Optional[float] = None
        try:
            from models.emly_sessions import EMLYSession

            sess = EMLYSession.select().where(
                (EMLYSession.bot_id == bot_id)
                & (EMLYSession.started_at >= from_date)
                & (EMLYSession.started_at <= to_date)
            )
            if channel_id:
                sess = sess.where(EMLYSession.channel_id == channel_id)
            sess_total = sess.count()
            if sess_total > 0:
                sess_resolved = sess.where(EMLYSession.is_resolved == True).count()  # noqa: E712
                resolution_rate = round(sess_resolved / sess_total, 4)
        except Exception:
            logging.debug("get_report: resolution_rate lookup skipped", exc_info=True)

        return {
            "actions": leads,
            "form_submission_timestamps": list_of_time_stamps,
            "users": users_count,
            "conversations": conversations,
            "engagement": engagement,
            "impressions": impressions,
            "short_impressions": short_impressions,
            "conversion_rate": conversion_rate,
            "messages": messages,
            "average_message_per_conversation": average_message_per_conversation,
            "average_conversations_per_user": average_conversations_per_user,
            # Phase 6/7/2 additions:
            "deflection_rate": deflection_rate,
            "deflection_count": deflection_total,
            "csat_avg": csat_avg,
            "csat_count": csat_count,
            "p95_latency_ms": p95_latency_ms,
            "resolution_rate": resolution_rate,
        }

    def get_messages_from_to(
        self,
        bot_id: str,
        from_timestamp: datetime = None,
        to_timestamp: datetime = None,
    ) -> List[EMLYMessageModel]:
        from_date = from_timestamp if from_timestamp else datetime.min
        to_date = to_timestamp if to_timestamp else datetime.max
        return [
            _row_to_model(m)
            for m in EMLYMessage.select().where(
                (EMLYMessage.bot == bot_id)
                & (EMLYMessage.created_on >= from_date)
                & (EMLYMessage.created_on <= to_date)
            )
            if m.expanded_query not in ("VIEW_BOT", "BOT_VIEWED")
        ]

    def get_messages_v2(
        self, bot_id: str, user_id: str, session_id: str
    ) -> List[EMLYMessageModel]:
        query = (
            EMLYMessage.select()
            .where(
                (EMLYMessage.bot == bot_id)
                & (EMLYMessage.user_id == user_id)
                & (EMLYMessage.session_id == session_id)
                & (
                    (
                        fn.LOWER(fn.TRIM(EMLYMessage.expanded_query)).not_in(
                            ["view_bot", "bot_viewed"]
                        )
                    )
                    | (EMLYMessage.expanded_query.is_null(True))
                    | (EMLYMessage.expanded_query == "")
                )
            )
            .order_by(EMLYMessage.created_on.desc())
        )
        return [_row_to_model(m) for m in query]


EMLYMessages = EMLYMessagesTable(DB)
