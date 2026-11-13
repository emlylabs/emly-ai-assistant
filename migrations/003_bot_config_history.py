"""Peewee migration -- 003_bot_config_history.py

Adds ``bot_config_history`` — append-only snapshots of ``bots.config_json``
written on every successful save. Drives the per-bot "History" tab so an
admin can see what the config looked like at point X and roll back to it.

Retention is capped at 50 versions per bot, enforced in the writer
(``services.bot_config.save_config_for_bot``). The migration only
creates the table — pruning is application-level.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    @migrator.create_model
    class BotConfigHistory(pw.Model):
        id = pw.CharField(max_length=64, primary_key=True)
        bot_id = pw.CharField(max_length=64, index=True)
        config_version = pw.IntegerField()
        config_json = pw.TextField()
        admin_id = pw.CharField(max_length=64, null=True)
        admin_email = pw.CharField(max_length=255, null=True)
        created_at = pw.DateTimeField()

        class Meta:
            table_name = "bot_config_history"
            indexes = ((("bot_id", "config_version"), True),)


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_model("bot_config_history")
