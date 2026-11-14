"""Peewee migration -- 011_message_search_index.py

The admin Conversations search matches ``emly_messages.message`` with
``ILIKE '%term%'``, which a btree index can't serve, so every search
scanned all of a bot's messages (twice: list + count). On Postgres, add a
``pg_trgm`` GIN index that ILIKE substring matches can use.

``pg_trgm`` is a trusted extension (PG 13+), so the database owner can
create it. If the role still can't, the index is skipped with a warning
rather than failing boot: search keeps working, just unindexed.

SQLite has no trigram index; nothing to do there.
"""
import logging

import peewee as pw
from peewee_migrate import Migrator

log = logging.getLogger(__name__)

_INDEX = "emly_messages_message_trgm_idx"


def _create_index(database: pw.Database) -> None:
    if not isinstance(database, pw.PostgresqlDatabase):
        return
    try:
        # Savepoint: a failed CREATE EXTENSION would otherwise abort the
        # migration's enclosing transaction.
        with database.atomic():
            database.execute_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    except Exception:
        log.warning(
            "pg_trgm unavailable; skipping %s (Conversations search stays unindexed)",
            _INDEX,
            exc_info=True,
        )
        return
    database.execute_sql(
        f"CREATE INDEX IF NOT EXISTS {_INDEX} "
        "ON emly_messages USING gin (message gin_trgm_ops)"
    )


def _drop_index(database: pw.Database) -> None:
    if isinstance(database, pw.PostgresqlDatabase):
        database.execute_sql(f"DROP INDEX IF EXISTS {_INDEX}")


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.run(_create_index, database)


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.run(_drop_index, database)
