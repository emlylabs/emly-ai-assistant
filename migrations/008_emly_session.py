"""Peewee migration -- 008_emly_session.py

Phase 4 of the backend-backfill plan (see ``backend-backfill.md``). Creates
the ``emly_session`` table that holds one row per ``(bot_id, session_id)``
pair so the admin UI's Conversations list can read session-level state
(channel, started_at, turn_count, resolution, sentiment) in O(1) instead
of GROUP-BY-deriving it from the messages table on every request.

The table is upserted by ``EMLYMessages.insert_new_message`` whenever a
new message lands. A separate one-time backfill (migration 009) walks
historical messages and seeds rows for sessions that predate this work.

Schema:
    id                  text PK    — same value as EMLYMessage.session_id.
    bot_id              text       — denormalised FK to bots.id.
    user_id             text       — first non-null user_id seen on the
                                     session.
    channel_id          text NULL  — first BotChannel.id observed; usually
                                     stable for the lifetime of a session.
    started_at          timestamp  — first message's created_on.
    last_activity_at    timestamp  — bumped on every new message.
    ended_at            timestamp NULL — set when admin marks resolved.
    turn_count          int        — running count of messages.
    is_resolved         bool NULL  — null = unmarked. Phase 6 populates.
    resolved_at         timestamp NULL
    resolved_by         text NULL  — admin_id or "auto".
    sentiment_score     real NULL  — Phase 8 populates (-1.0..+1.0).
    sentiment_label     text NULL  — "negative" / "neutral" / "positive".
    intent              text NULL
    intent_confidence   real NULL
    enrichment_at       timestamp NULL — last enrichment run.

Indexes for the common reader queries: by bot+started, by bot+last_activity,
by bot+channel, by bot+resolved.
"""

from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext  # noqa: F401


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    @migrator.create_model
    class EMLYSession(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot_id = pw.CharField(max_length=64)
        user_id = pw.CharField(max_length=255)
        channel_id = pw.CharField(max_length=64, null=True)
        started_at = pw.DateTimeField()
        last_activity_at = pw.DateTimeField()
        ended_at = pw.DateTimeField(null=True)
        turn_count = pw.IntegerField(default=0)
        is_resolved = pw.BooleanField(null=True)
        resolved_at = pw.DateTimeField(null=True)
        resolved_by = pw.CharField(max_length=64, null=True)
        sentiment_score = pw.FloatField(null=True)
        sentiment_label = pw.CharField(max_length=32, null=True)
        intent = pw.CharField(max_length=128, null=True)
        intent_confidence = pw.FloatField(null=True)
        enrichment_at = pw.DateTimeField(null=True)

        class Meta:
            table_name = "emly_session"

    migrator.add_index("emly_session", "bot_id", "started_at")
    migrator.add_index("emly_session", "bot_id", "last_activity_at")
    migrator.add_index("emly_session", "bot_id", "channel_id")
    migrator.add_index("emly_session", "bot_id", "is_resolved")


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_model("emly_session")
