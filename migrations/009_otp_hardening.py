"""Peewee migration -- 009_otp_hardening.py

Hardens widget one-time passcodes (``otp_auth``):

* ``expires_at`` — absolute expiry stamped at issue time. Previously the
  expiry was computed from *now* at verification, so codes never expired.
* ``attempts`` — failed-verification counter; the code is invalidated
  after the limit in ``services.otp``.
* ``otp`` now stores an HMAC-SHA256 of the code (keyed from
  ``BOT_SECRETS_KEY``) instead of the plaintext.

Existing rows hold plaintext codes, so they are deleted. Outstanding codes
are at most minutes old; affected users simply request a new one.

Raw SQL for the same reason as 008: identical behaviour on SQLite and
Postgres without peewee_migrate's extra index work.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.sql("DELETE FROM otp_auth")
    migrator.sql("ALTER TABLE otp_auth ADD COLUMN expires_at TIMESTAMP")
    migrator.sql("ALTER TABLE otp_auth ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    # Hashed codes are useless to the old plaintext comparison; drop them.
    migrator.sql("DELETE FROM otp_auth")
    migrator.remove_fields("otp_auth", "expires_at", "attempts")
