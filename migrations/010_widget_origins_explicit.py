"""Peewee migration -- 010_widget_origins_explicit.py

B3: the schema default for ``limits.widget_allowed_origins`` changes from
``["*"]`` (any site may embed the widget) to ``[]`` (only this app's own
origin — the admin preview and hosted test page).

A config that omits the key picks up the schema default when it is read,
so changing the default would silently lock every existing bot that never
set its origins explicitly out of the sites it is embedded on. This
migration keeps existing bots' current behaviour by writing ``["*"]``
explicitly wherever the key is missing — in the live config, the draft,
and config-history snapshots (so restoring an old version doesn't change
its origins either). Owners see a warning in the admin UI until they
replace ``*`` with their real sites. New bots get the safe default.

Rows whose JSON can't be parsed are left untouched (boot-time config
validation already reports them).
"""
import json

import peewee as pw
from peewee_migrate import Migrator

_KEY = "widget_allowed_origins"


def _with_explicit_wildcard(raw):
    """Return the new JSON text, or None when no change is needed."""
    if not raw:
        return None
    try:
        cfg = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(cfg, dict):
        return None
    limits = cfg.get("limits")
    if limits is None:
        limits = {}
    if not isinstance(limits, dict) or _KEY in limits:
        return None
    limits[_KEY] = ["*"]
    cfg["limits"] = limits
    return json.dumps(cfg)


def _backfill(database: pw.Database, table: str, columns) -> None:
    p = database.param
    cols = ", ".join(columns)
    for row in database.execute_sql(f"SELECT id, {cols} FROM {table}").fetchall():
        row_id, values = row[0], row[1:]
        for column, raw in zip(columns, values):
            updated = _with_explicit_wildcard(raw)
            if updated is not None:
                database.execute_sql(f"UPDATE {table} SET {column} = {p} WHERE id = {p}", (updated, row_id))


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.run(_backfill, database, "bots", ("config_json", "config_draft_json"))
    migrator.run(_backfill, database, "bot_config_history", ("config_json",))


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    # Explicit ["*"] is equivalent to the old default; nothing to undo.
    pass
