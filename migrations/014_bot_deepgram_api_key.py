"""Peewee migration -- 014_bot_deepgram_api_key.py

Per-bot Deepgram API key (encrypted at rest) for widget STT/TTS.
Falls back to deployment ``DEEPGRAM_API_KEY`` when unset.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.add_fields(
        "bots",
        deepgram_api_key_encrypted=pw.TextField(null=True),
    )


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_fields("bots", "deepgram_api_key_encrypted")
