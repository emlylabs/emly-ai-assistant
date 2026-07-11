"""Idempotency table for retried platform webhooks.

Slack/Teams/Google Chat retry on non-2xx (and Slack on slow ack). The
dispatcher writes ``(channel_type, event_id)`` here before running the
handler; a duplicate insert means "already processed."

State machine — ``done`` / ``processing`` / ``failed``:

- ``processing`` rows have a ``processing_expires_at``. A retry that
  arrives while the original worker is still running gets dropped
  (concurrent dedupe). If the original crashed and the expiry passed,
  we reclaim the row and let the retry run — at-least-once delivery
  rather than at-most-once with silent message loss.
- ``failed`` rows are reclaimable too — operator can replay by deleting
  the row, or a retry naturally re-runs.
- A daily cron deletes rows older than ``WEBHOOK_DEDUPE_RETENTION_HOURS``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from peewee import (
    AutoField,
    CharField,
    DateTimeField,
    DoesNotExist,
    IntegerField,
    IntegrityError,
    Model,
)

from db.db import DB

log = logging.getLogger(__name__)

# Maximum time a "processing" row holds the lock before it's considered
# crashed and reclaimable. Picked to comfortably exceed agent latency
# while still letting genuine retries through within seconds.
PROCESSING_TTL = timedelta(minutes=5)


class WebhookEventDedupe(Model):
    id = AutoField()
    channel_type = CharField(max_length=64)
    event_id = CharField(max_length=255)
    received_at = DateTimeField()
    status = CharField(max_length=32, default="processing")
    processing_expires_at = DateTimeField(null=True)
    attempts = IntegerField(default=1)

    class Meta:
        database = DB
        table_name = "webhook_event_dedupe"
        indexes = (
            (("channel_type", "event_id"), True),
            (("received_at",), False),
        )


class WebhookEventDedupeTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([WebhookEventDedupe])

    def claim(self, channel_type: str, event_id: str) -> Tuple[bool, str]:
        """Two-phase claim.

        Returns ``(claimed, status)``:
        - ``(True, "new")``      — first time we've seen this event
        - ``(True, "reclaimed")``— previous worker crashed; we get to retry
        - ``(False, "concurrent")`` — another worker is processing
        - ``(False, "done")``    — already processed; drop
        """
        now = datetime.utcnow()
        try:
            WebhookEventDedupe.create(
                channel_type=channel_type,
                event_id=event_id,
                received_at=now,
                status="processing",
                processing_expires_at=now + PROCESSING_TTL,
                attempts=1,
            )
            return True, "new"
        except IntegrityError:
            pass

        try:
            row = WebhookEventDedupe.get(
                (WebhookEventDedupe.channel_type == channel_type)
                & (WebhookEventDedupe.event_id == event_id)
            )
        except DoesNotExist:
            return True, "new"

        if row.status == "done":
            return False, "done"

        # Race-safe reclaim: two retries arriving after a stale "processing"
        # row both see expired and both want to take over. Use a conditional
        # UPDATE that includes the OLD status + processing_expires_at in the
        # WHERE clause so the database adjudicates — exactly one writer
        # gets ``rows == 1``, the loser gets ``rows == 0`` and re-classifies
        # as concurrent.
        if row.status == "processing":
            if row.processing_expires_at and row.processing_expires_at > now:
                return False, "concurrent"
            old_expires = row.processing_expires_at
            old_attempts = row.attempts or 0
            where = (
                (WebhookEventDedupe.channel_type == channel_type)
                & (WebhookEventDedupe.event_id == event_id)
                & (WebhookEventDedupe.status == "processing")
                & (WebhookEventDedupe.attempts == old_attempts)
            )
            if old_expires is None:
                where = where & WebhookEventDedupe.processing_expires_at.is_null()
            else:
                where = where & (WebhookEventDedupe.processing_expires_at == old_expires)
            rows = (
                WebhookEventDedupe.update(
                    processing_expires_at=now + PROCESSING_TTL,
                    attempts=old_attempts + 1,
                    received_at=now,
                )
                .where(where)
                .execute()
            )
            return (True, "reclaimed") if rows == 1 else (False, "concurrent")

        # ``failed``: let one (and only one) retry transition it back to
        # processing. Same conditional-UPDATE technique guards against two
        # concurrent retries both reclaiming a failed row.
        old_attempts = row.attempts or 0
        rows = (
            WebhookEventDedupe.update(
                status="processing",
                processing_expires_at=now + PROCESSING_TTL,
                attempts=old_attempts + 1,
                received_at=now,
            )
            .where(
                (WebhookEventDedupe.channel_type == channel_type)
                & (WebhookEventDedupe.event_id == event_id)
                & (WebhookEventDedupe.status == "failed")
                & (WebhookEventDedupe.attempts == old_attempts)
            )
            .execute()
        )
        return (True, "reclaimed") if rows == 1 else (False, "concurrent")

    def mark_done(self, channel_type: str, event_id: str) -> None:
        WebhookEventDedupe.update(
            status="done",
            processing_expires_at=None,
        ).where(
            (WebhookEventDedupe.channel_type == channel_type)
            & (WebhookEventDedupe.event_id == event_id)
        ).execute()

    def mark_failed(self, channel_type: str, event_id: str) -> None:
        WebhookEventDedupe.update(
            status="failed",
            processing_expires_at=None,
        ).where(
            (WebhookEventDedupe.channel_type == channel_type)
            & (WebhookEventDedupe.event_id == event_id)
        ).execute()

    def cleanup(self, older_than: timedelta = timedelta(days=1)) -> int:
        cutoff = datetime.utcnow() - older_than
        return WebhookEventDedupe.delete().where(WebhookEventDedupe.received_at < cutoff).execute()


WebhookEventDedupes = WebhookEventDedupeTable(DB)
