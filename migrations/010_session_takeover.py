"""Peewee migration -- 010_session_takeover.py

Phase 10 of the backend-backfill plan: add take-over columns to
``emly_session`` so an admin can pause the bot and reply directly to the
end user (web_widget channel only in v1).

Schema:
    taken_over_by      text NULL  — admin_id of the person who took
                                     over. Null = bot is replying.
    taken_over_at      timestamp NULL
    taken_over_until   timestamp NULL — optional auto-release window.
"""

from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext  # noqa: F401


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.add_fields(
        "emly_session",
        taken_over_by=pw.CharField(max_length=64, null=True),
        taken_over_at=pw.DateTimeField(null=True),
        taken_over_until=pw.DateTimeField(null=True),
    )


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_fields(
        "emly_session",
        "taken_over_by",
        "taken_over_at",
        "taken_over_until",
    )
