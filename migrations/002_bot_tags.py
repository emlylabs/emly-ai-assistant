"""Peewee migration -- 002_bot_tags.py

Adds a free-form ``tags`` column to ``bots`` so admins can group bots
(e.g. "client:acme", "env:prod") on the workspace overview. Stored as
JSON text for backend portability across SQLite and Postgres.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.add_fields("bots", tags=pw.TextField(null=True))


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_fields("bots", "tags")
