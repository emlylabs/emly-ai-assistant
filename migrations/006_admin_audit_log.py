"""Peewee migration -- 006_admin_audit_log.py

Phase 10 of the auth-rewrite plan. Adds the ``admin_audit_log`` table that
records every authn/authz success and failure plus all admin/bot mutations.

Schema:
    id              text PK
    admin_id        text NULL  — null when the actor is unauthenticated
                                  (failed login, anonymous CSRF rejection, etc.)
    bot_id          text NULL  — null for cross-bot events (auth, admin lifecycle)
    action          text       — dotted code: "auth.login", "authz.denied", …
    target_type     text NULL  — "admin" / "bot" / "membership" / …
    target_id       text NULL  — id of the affected entity, when applicable
    payload         text NULL  — JSON-encoded ad-hoc context
    ip              text NULL
    ua              text NULL  — user-agent
    success         boolean    — false → the action was denied / failed
    created_at      timestamp

Indexed for the three common reader queries: by admin, by bot, by action.
"""

from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext  # noqa: F401


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    @migrator.create_model
    class AdminAuditLog(pw.Model):
        id = pw.CharField(max_length=64, primary_key=True)
        admin_id = pw.CharField(max_length=64, null=True)
        bot_id = pw.CharField(max_length=64, null=True)
        action = pw.CharField(max_length=128)
        target_type = pw.CharField(max_length=64, null=True)
        target_id = pw.CharField(max_length=255, null=True)
        payload = pw.TextField(null=True)
        ip = pw.CharField(max_length=64, null=True)
        ua = pw.CharField(max_length=512, null=True)
        success = pw.BooleanField(default=True)
        created_at = pw.DateTimeField()

        class Meta:
            table_name = "admin_audit_log"

    migrator.add_index("admin_audit_log", "admin_id", "created_at")
    migrator.add_index("admin_audit_log", "bot_id", "created_at")
    migrator.add_index("admin_audit_log", "action", "created_at")


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_model("admin_audit_log")
