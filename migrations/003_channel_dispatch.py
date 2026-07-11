"""Peewee migration -- 003_channel_dispatch.py

Channel dispatch foundations:

1. ``bot_channel`` gets ``secrets_rotated_at`` (when secrets last
   changed — admin UI surfaces this), ``display_name`` (operator-facing
   label, e.g. ``@MyBot`` for Telegram), and ``config_version`` (for
   optimistic-concurrency on token refresh).

2. ``webhook_event_dedupe`` becomes a state machine:
   ``status`` ∈ {``processing``, ``done``, ``failed``},
   ``processing_expires_at`` so a worker that crashed mid-dispatch
   doesn't permanently block retries, and ``attempts`` for visibility.
"""
from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext  # noqa: F401


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    # ------------------------------------------------------------------
    # bot_channel additions
    # ------------------------------------------------------------------
    migrator.add_fields(
        "bot_channel",
        secrets_rotated_at=pw.DateTimeField(null=True),
        display_name=pw.CharField(max_length=255, null=True),
        config_version=pw.IntegerField(default=0),
    )

    # ------------------------------------------------------------------
    # webhook_event_dedupe state machine
    # ------------------------------------------------------------------
    migrator.add_fields(
        "webhook_event_dedupe",
        status=pw.CharField(max_length=32, default="done"),
        processing_expires_at=pw.DateTimeField(null=True),
        attempts=pw.IntegerField(default=1),
    )


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_fields("webhook_event_dedupe", "status", "processing_expires_at", "attempts")
    migrator.remove_fields("bot_channel", "secrets_rotated_at", "display_name", "config_version")
