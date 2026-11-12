"""Peewee migration -- 007_admin_soft_delete.py

Adds ``deleted_at`` to ``admin_user`` for soft-delete.

Hard-deleting admins severed referential integrity from
``admin_audit_log.admin_id`` (the row gone, the audit trail orphaned).
Soft-delete preserves the row for forensics while logins are refused
by ``services/auth/user_resolver``.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.add_fields(
        "admin_user",
        deleted_at=pw.DateTimeField(null=True),
    )


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_fields("admin_user", "deleted_at")
