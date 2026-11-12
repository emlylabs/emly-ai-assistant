"""Peewee migration -- 008_space_invites_and_default_space.py

Adds explicit lifecycle to ``pending_admin`` and default-space tracking
to ``bots``. Together these support the new invite UX where:

* a bot owner (not just a superadmin) can invite a new email to their
  space; the row is minted with a ``token`` and an invitation email is
  sent;
* recipients explicitly accept or reject from a tokenised landing page;
* the first OIDC login auto-creates a default space (``is_default``)
  with a one-shot rename (``default_renamed``).

Legacy ``pending_admin`` rows are backfilled to
``kind='superadmin_legacy'`` so the existing silent-consume path keeps
working unchanged — only ``space_invite`` rows go through the new
accept/reject flow.

The original ``unique(email)`` constraint on ``pending_admin`` is dropped
because a single email may now have multiple concurrent invites (one per
space). Uniqueness moves to ``token``.

Implementation note — raw SQL is used throughout. peewee_migrate's
``add_fields`` triggers extra index work on SQLite that interferes with
the new indexes we add ourselves; doing this by hand keeps the
behaviour identical on SQLite and Postgres.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    # ------------------------------------------------------------------
    # pending_admin — explicit lifecycle + URL token
    # ------------------------------------------------------------------
    migrator.sql("ALTER TABLE pending_admin ADD COLUMN token VARCHAR(64)")
    migrator.sql(
        "ALTER TABLE pending_admin ADD COLUMN status VARCHAR(16) "
        "NOT NULL DEFAULT 'pending'"
    )
    migrator.sql(
        "ALTER TABLE pending_admin ADD COLUMN kind VARCHAR(24) "
        "NOT NULL DEFAULT 'superadmin_legacy'"
    )
    migrator.sql("ALTER TABLE pending_admin ADD COLUMN target_bot_id VARCHAR(64)")
    migrator.sql("ALTER TABLE pending_admin ADD COLUMN responded_at TIMESTAMP")
    migrator.sql("ALTER TABLE pending_admin ADD COLUMN responded_by VARCHAR(64)")

    # Backfill: rows already consumed are in the terminal "accepted"
    # state; mirror consumed_at/by into responded_at/by for unified
    # reporting. Pending rows keep the column default.
    migrator.sql(
        "UPDATE pending_admin SET status='accepted', "
        "responded_at=consumed_at, responded_by=consumed_by "
        "WHERE consumed_at IS NOT NULL"
    )

    # Swap the unique(email) constraint for non-unique(email) plus
    # unique(token). The baseline declared ``email`` with ``unique=True``
    # on the Peewee model, which named the auto-index ``pendingadmin_email``.
    migrator.sql('DROP INDEX IF EXISTS "pendingadmin_email"')
    migrator.sql(
        'CREATE INDEX IF NOT EXISTS "pending_admin_email" '
        'ON "pending_admin" ("email")'
    )
    migrator.sql(
        'CREATE UNIQUE INDEX IF NOT EXISTS "pending_admin_token" '
        'ON "pending_admin" ("token")'
    )
    migrator.sql(
        'CREATE INDEX IF NOT EXISTS "pending_admin_status" '
        'ON "pending_admin" ("status")'
    )

    # ------------------------------------------------------------------
    # bots — default-space + rename-lock
    # ------------------------------------------------------------------
    migrator.sql(
        "ALTER TABLE bots ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0"
    )
    migrator.sql("ALTER TABLE bots ADD COLUMN default_for_admin_id VARCHAR(64)")
    migrator.sql(
        "ALTER TABLE bots ADD COLUMN default_renamed INTEGER NOT NULL DEFAULT 0"
    )
    migrator.sql(
        'CREATE UNIQUE INDEX IF NOT EXISTS "bots_default_for_admin_id" '
        'ON "bots" ("default_for_admin_id")'
    )


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.sql('DROP INDEX IF EXISTS "bots_default_for_admin_id"')
    migrator.sql('DROP INDEX IF EXISTS "pending_admin_status"')
    migrator.sql('DROP INDEX IF EXISTS "pending_admin_token"')
    migrator.sql('DROP INDEX IF EXISTS "pending_admin_email"')
    # Recreate the original unique(email) for symmetry.
    migrator.sql(
        'CREATE UNIQUE INDEX IF NOT EXISTS "pendingadmin_email" '
        'ON "pending_admin" ("email")'
    )
    # peewee_migrate's remove_fields is correct on Postgres (column drop)
    # and on modern SQLite (DROP COLUMN). We keep that path here.
    migrator.remove_fields(
        "bots",
        "is_default",
        "default_for_admin_id",
        "default_renamed",
    )
    migrator.remove_fields(
        "pending_admin",
        "token",
        "status",
        "kind",
        "target_bot_id",
        "responded_at",
        "responded_by",
    )
