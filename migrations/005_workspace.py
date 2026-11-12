"""Peewee migration -- 005_workspace.py

Adds two workspace-scoped tables for §6 of `improve-ux.md`:

- ``workspace_settings`` — single-row key-value JSON store. Holds
  workspace-wide defaults (default theme tokens, default LLM provider,
  default limits) that new bots inherit and the workspace overview
  exposes. One row keyed by ``id='singleton'``; we don't shard
  workspaces today, but the schema doesn't force that assumption
  forever.

- ``workspace_bot_templates`` — admin-saved bot templates. ``config_json``
  is a frozen snapshot of a real bot's config_json; the create wizard
  lists these alongside the code-baked starter templates. Soft-state by
  design: deleting a template doesn't touch the bots that were created
  from it.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    @migrator.create_model
    class WorkspaceSettings(pw.Model):
        id = pw.CharField(max_length=64, primary_key=True)
        settings_json = pw.TextField(null=True)
        updated_at = pw.DateTimeField()
        updated_by_admin_id = pw.CharField(max_length=64, null=True)

        class Meta:
            table_name = "workspace_settings"

    @migrator.create_model
    class WorkspaceBotTemplate(pw.Model):
        id = pw.CharField(max_length=64, primary_key=True)
        name = pw.CharField(max_length=255)
        description = pw.CharField(max_length=512, null=True)
        config_json = pw.TextField()
        created_by_admin_id = pw.CharField(max_length=64, null=True)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()

        class Meta:
            table_name = "workspace_bot_templates"


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_model("workspace_settings")
    migrator.remove_model("workspace_bot_templates")
