"""Peewee migration -- 012_channel_dm_carryover.py

Slack and Google Chat DMs used to be keyed on the thread of each top-level
message; they are now one running conversation per person. A DM lead in
progress at deploy lives under an old per-thread session id that the new
key can't reach, and nothing stored tells which old session belonged to
which DM. So flag every Slack / Google Chat session that is still open
right now (unresolved, not ended, active within the idle window). The
dispatcher continues a sender's flagged session from their DM and clears
the flag once the session is resolved, closed or idle, so the flag only
lives through the changeover.

Sessions that start after this migration are never flagged.
"""
import os
from datetime import datetime, timedelta

import peewee as pw
from peewee_migrate import Migrator

# Same default as ``config.CHANNEL_SESSION_IDLE_HOURS``; read here directly
# because importing ``config`` loads the embedding models.
_IDLE_HOURS = float(os.getenv("CHANNEL_SESSION_IDLE_HOURS", 24))


def _flag_open_sessions(database: pw.Database) -> None:
    p = database.param
    since = datetime.utcnow() - timedelta(hours=_IDLE_HOURS)
    database.execute_sql(
        "UPDATE emly_session SET dm_carryover = " + p + " "
        "WHERE channel_id IN (SELECT id FROM bot_channel WHERE type IN ('slack', 'google_chat')) "
        "AND (is_resolved IS NULL OR is_resolved = " + p + ") "
        "AND ended_at IS NULL "
        "AND last_activity_at >= " + p,
        (True, False, since),
    )


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.add_fields("emly_session", dm_carryover=pw.BooleanField(null=True))
    migrator.run(_flag_open_sessions, database)


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_fields("emly_session", "dm_carryover")
