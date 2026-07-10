"""Peewee migration -- 004_admin_oidc_reshape.py

Phase 3 of the auth-rewrite plan. Additive — does not drop ``password_hash``
or any existing column. The legacy ``services/auth_service.py`` keeps working
until Phase 9 deletes it.

Adds to ``admin_user``:
    issuer          VARCHAR(512) NULL    — OIDC issuer the admin authenticated against
    subject         VARCHAR(255) NULL    — IdP `sub` claim
    email_verified  BOOLEAN      NOT NULL DEFAULT false
    is_superadmin   BOOLEAN      NOT NULL DEFAULT false
    last_login_at   TIMESTAMPTZ  NULL
    name            VARCHAR(255) NULL

Adds composite unique index on ``(issuer, subject)``. NULL pairs are allowed
(legacy rows that haven't yet been linked to an IdP) per SQL UNIQUE semantics.

Creates ``pending_admin`` for pre-staged invitees:
    id, email UNIQUE, invited_by, is_superadmin, bot_assignments JSON,
    created_at, expires_at NULL, consumed_at NULL, consumed_by NULL.

Phase 9 will drop ``password_hash`` once the embedded issuer fully owns auth.
"""
from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext  # noqa: F401


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    # ------------------------------------------------------------------
    # admin_user — additive columns. ``password_hash`` keeps its NOT NULL
    # constraint (changing it on SQLite requires a full table-rebuild that
    # peewee_migrate doesn't do reliably). The OIDC path inserts the empty
    # string for ``password_hash`` instead — ``verify_password`` rejects
    # empty hashes, so legacy login can't authenticate as an OIDC-only admin.
    # ------------------------------------------------------------------
    migrator.add_fields(
        "admin_user",
        issuer=pw.CharField(max_length=512, null=True),
        subject=pw.CharField(max_length=255, null=True),
        email_verified=pw.BooleanField(default=False),
        is_superadmin=pw.BooleanField(default=False),
        last_login_at=pw.DateTimeField(null=True),
        name=pw.CharField(max_length=255, null=True),
    )
    # Composite unique on (issuer, subject) — NULL pairs are allowed by SQL
    # UNIQUE semantics, so legacy rows pre-IdP-link don't conflict.
    migrator.add_index("admin_user", "issuer", "subject", unique=True)

    # ------------------------------------------------------------------
    # pending_admin — pre-staged invitees, consumed on first matching IdP login
    # ------------------------------------------------------------------
    @migrator.create_model
    class PendingAdmin(pw.Model):
        id = pw.CharField(max_length=64, primary_key=True)
        email = pw.CharField(max_length=255, unique=True)
        invited_by = pw.CharField(max_length=64, null=True)
        is_superadmin = pw.BooleanField(default=False)
        bot_assignments = pw.TextField(null=True)  # JSON-encoded list of {bot_id, role}
        created_at = pw.DateTimeField()
        expires_at = pw.DateTimeField(null=True)
        consumed_at = pw.DateTimeField(null=True)
        consumed_by = pw.CharField(max_length=64, null=True)

        class Meta:
            table_name = "pending_admin"


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_model("pending_admin")
    migrator.drop_index("admin_user", "issuer", "subject")
    migrator.remove_fields(
        "admin_user",
        "issuer",
        "subject",
        "email_verified",
        "is_superadmin",
        "last_login_at",
        "name",
    )
