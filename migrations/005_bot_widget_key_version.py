"""Peewee migration -- 005_bot_widget_key_version.py

Adds the per-bot widget-token key-rotation columns.

The widget HMAC subkey is derived as:

    HKDF(BOT_SECRETS_KEY, salt=f"{bot_id}:v{widget_key_version}",
         info=b"widget-token-v1", length=32)

Bumping ``widget_key_version`` (via ``Bots.rotate_widget_key``) invalidates
all live widget tokens for that bot. ``widget_key_rotated_at`` lets callers
implement a grace window — accept the previous version for some time after
rotation so in-flight chats don't break.
"""

from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext  # noqa: F401


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.add_fields(
        "bots",
        widget_key_version=pw.IntegerField(default=1),
        widget_key_rotated_at=pw.DateTimeField(null=True),
    )


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_fields("bots", "widget_key_version", "widget_key_rotated_at")
