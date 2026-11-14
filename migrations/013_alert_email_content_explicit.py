"""Peewee migration -- 013_alert_email_content_explicit.py

G1c: the schema default for ``alert_email_content`` changes from ``full``
(AI summary, transcript, visitor details and attachments) to
``link_only`` (just a link to the conversation in the admin console), so
new bots keep visitor data out of mailboxes.

A config that omits the key picks up the schema default when it is read,
so without this migration every existing bot that never chose a mode
would silently switch to link-only alerts. As migration 010 did for
widget origins, write ``full`` explicitly wherever the key is missing: in
the live config, the draft, and config-history snapshots (so restoring an
old version doesn't change its alerts either).

Workspace templates are left alone: they describe new bots, which get the
new default.

Rows whose JSON can't be parsed are left untouched (boot-time config
validation already reports them).
"""
import json

import peewee as pw
from peewee_migrate import Migrator

_KEY = "alert_email_content"


def _with_explicit_full(raw):
    """Return the new JSON text, or None when no change is needed."""
    if not raw:
        return None
    try:
        cfg = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(cfg, dict) or _KEY in cfg:
        return None
    cfg[_KEY] = "full"
    return json.dumps(cfg)


def _backfill(database: pw.Database, table: str, columns) -> None:
    p = database.param
    cols = ", ".join(columns)
    for row in database.execute_sql(f"SELECT id, {cols} FROM {table}").fetchall():
        row_id, values = row[0], row[1:]
        for column, raw in zip(columns, values):
            updated = _with_explicit_full(raw)
            if updated is not None:
                database.execute_sql(f"UPDATE {table} SET {column} = {p} WHERE id = {p}", (updated, row_id))


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.run(_backfill, database, "bots", ("config_json", "config_draft_json"))
    migrator.run(_backfill, database, "bot_config_history", ("config_json",))


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    # Explicit "full" is equivalent to the old default; nothing to undo.
    pass
