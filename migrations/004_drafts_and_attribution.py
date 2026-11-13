"""Peewee migration -- 004_drafts_and_attribution.py

Adds a draft slot + last-saved attribution to ``bots``:

- ``config_draft_json`` — nullable JSON blob. When non-null, holds an
  in-progress config that the public widget ignores; only the admin
  Test/Preview surfaces read from it. Promoting (publishing) the draft
  clears this column and overwrites ``config_json``.
- ``config_draft_updated_at`` / ``config_draft_updated_by_admin_id`` —
  attribution + freshness on the latest draft save.
- ``last_updated_by_admin_id`` / ``last_updated_by_admin_email`` — who
  last wrote ``config_json``. Surfaces as the "Last saved by …" footer
  in the config editor.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.add_fields(
        "bots",
        config_draft_json=pw.TextField(null=True),
        config_draft_updated_at=pw.DateTimeField(null=True),
        config_draft_updated_by_admin_id=pw.CharField(max_length=64, null=True),
        last_updated_by_admin_id=pw.CharField(max_length=64, null=True),
        last_updated_by_admin_email=pw.CharField(max_length=255, null=True),
    )


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_fields(
        "bots",
        "config_draft_json",
        "config_draft_updated_at",
        "config_draft_updated_by_admin_id",
        "last_updated_by_admin_id",
        "last_updated_by_admin_email",
    )
