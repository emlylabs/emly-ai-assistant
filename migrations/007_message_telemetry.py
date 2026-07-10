"""Peewee migration -- 007_message_telemetry.py

Phase 1 of the backend-backfill plan (see ``backend-backfill.md``). Adds
nullable telemetry, channel-link, deflection, and CSAT-rating columns to
the ``emly_messages`` table so subsequent phases can populate them
without further schema migrations.

All columns are nullable; existing rows keep ``NULL``. No reads or writes
in the current code path consume these fields yet — Phase 2 (LLM
telemetry capture), Phase 3 (channel threading), Phase 6 (deflection)
and Phase 7 (CSAT) wire them up. Until then this migration is a pure
schema lift with zero behavioural impact.

Schema additions:
    channel_id          text NULL   — BotChannel.id (not a FK so a
                                       channel deletion doesn't cascade
                                       through historical messages).
    model_used          text NULL   — e.g. "openai/gpt-4o", "admin:<id>"
                                       for human takeover replies.
    prompt_tokens       int  NULL
    completion_tokens   int  NULL
    response_time_ms    int  NULL   — wall-clock latency of the LLM call.
    is_deflected        bool NULL   — null = unmeasured.
    deflection_method   text NULL   — "heuristic" / "admin" / "classifier"
                                       so audits can tell where the flag
                                       came from.
    rating              int  NULL   — -1 / 0 / 1 (thumbs down / unrated /
                                       thumbs up). Set by the public
                                       widget rate endpoint or admin
                                       override.
    rated_at            timestamp NULL
    citations           text NULL   — JSON-encoded list of citation
                                       metadata (file_id, filename,
                                       source_url, score). Stored as
                                       text for SQLite portability;
                                       readers parse on demand.

Indexes:
    (bot, channel_id)               — channel-filtered conversation lists.
    (bot, rating)                   — CSAT aggregations per bot.
"""

from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext  # noqa: F401


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.add_fields(
        "emly_messages",
        channel_id=pw.CharField(max_length=64, null=True),
        model_used=pw.CharField(max_length=128, null=True),
        prompt_tokens=pw.IntegerField(null=True),
        completion_tokens=pw.IntegerField(null=True),
        response_time_ms=pw.IntegerField(null=True),
        is_deflected=pw.BooleanField(null=True),
        deflection_method=pw.CharField(max_length=32, null=True),
        rating=pw.IntegerField(null=True),
        rated_at=pw.DateTimeField(null=True),
        citations=pw.TextField(null=True),
    )
    # `bot` is the ForeignKeyField on `EMLYMessage`; peewee_migrate's
    # `add_index` resolves columns by field name, not column name (see
    # `migrations/001_initial.py:154-157` — the original indexes use
    # `bot`, not `bot_id`). Using the column name raises
    # `AttributeError: 'NoneType' object has no attribute 'column_name'`.
    migrator.add_index("emly_messages", "bot", "channel_id")
    migrator.add_index("emly_messages", "bot", "rating")


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_fields(
        "emly_messages",
        "channel_id",
        "model_used",
        "prompt_tokens",
        "completion_tokens",
        "response_time_ms",
        "is_deflected",
        "deflection_method",
        "rating",
        "rated_at",
        "citations",
    )
