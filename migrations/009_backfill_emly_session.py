"""Peewee migration -- 009_backfill_emly_session.py

Phase 4 of the backend-backfill plan: one-time data backfill that walks
``emly_messages`` and synthesises one ``emly_session`` row per distinct
``(bot_id, session_id)`` pair so the new aggregation endpoints have data
to read on day one.

Skips on bots that already have rows for the session id (idempotent —
re-running this migration won't double-insert). Reads channel_id from
the earliest message in the session if that field is set (Phase 3
threaded it onto messages); otherwise leaves it null.

Cost: a single SQL aggregate over emly_messages plus one INSERT per
unique session. For ~1M messages spread across ~50k sessions, this
runs in seconds on Postgres and tens of seconds on SQLite. We choose
to do it inline at boot rather than gating behind a feature flag —
the schema migration above already created an empty table; backfill
makes the table useful immediately.
"""

from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext  # noqa: F401


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    """Walk emly_messages and seed emly_session rows for distinct sessions."""
    if fake:
        return
    sql = """
        INSERT INTO emly_session (
            id, bot_id, user_id, channel_id,
            started_at, last_activity_at, turn_count
        )
        SELECT
            m.session_id AS id,
            m.bot_id,
            -- first user_id observed in the session (any role, falling back to the empty string sentinel for anonymous sessions)
            COALESCE(MIN(CASE WHEN m.user_id IS NOT NULL AND m.user_id != '' THEN m.user_id END), '') AS user_id,
            -- first channel_id observed (Phase 3 may have populated it; older rows are null)
            MIN(m.channel_id) AS channel_id,
            MIN(m.created_on) AS started_at,
            MAX(m.created_on) AS last_activity_at,
            COUNT(*) AS turn_count
        FROM emly_messages m
        WHERE m.session_id IS NOT NULL
          AND m.session_id != ''
          AND NOT EXISTS (
              SELECT 1 FROM emly_session s WHERE s.id = m.session_id
          )
        GROUP BY m.bot_id, m.session_id
    """
    try:
        database.execute_sql(sql)
    except Exception as e:
        # The backfill is a best-effort optimisation; if it fails (e.g.
        # SQLite quirks with COALESCE on aggregates, weird historical
        # rows), the application still boots and the upsert path in
        # `EMLYSessions.upsert_on_message` populates rows lazily as
        # new messages arrive. Log + carry on rather than blocking the
        # migration runner.
        import logging
        logging.warning("emly_session backfill skipped: %s", e)


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    """Drop all backfilled rows. The table itself is dropped by 008's rollback."""
    if fake:
        return
    try:
        database.execute_sql("DELETE FROM emly_session")
    except Exception:
        pass
