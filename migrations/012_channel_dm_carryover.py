"""Peewee migration -- 012_channel_dm_carryover.py

Intentionally a no-op. Keep this file.

An earlier version of this migration (282494f, removed in e071e22 before
release) added a nullable ``emly_session.dm_carryover`` column and flagged
open Slack / Google Chat sessions. Databases that booted in between record
``012_channel_dm_carryover`` as applied, and peewee_migrate replays every
applied migration before it runs a new one, so without this file the next
migration fails boot there with ``FileNotFoundError``.

On those databases the leftover column is nullable and unused, so it is
harmless and left in place. Fresh databases record this migration and
change nothing.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    pass


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    pass
