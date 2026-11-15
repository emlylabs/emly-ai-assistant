"""Every migration a database may have recorded must still have a file.

peewee_migrate replays all applied migrations before running a new one,
so a database that recorded a since-deleted migration fails boot as soon
as the next migration ships. ``012_channel_dm_carryover`` was added and
then removed during PR #11; any database that booted in between has it.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import peewee as pw
from peewee_migrate import Router

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _router(tmp_path) -> Router:
    db = pw.SqliteDatabase(str(tmp_path / "m.db"))
    return Router(db, migrate_dir=MIGRATIONS)


def test_database_that_ran_the_removed_dm_carryover_migration_still_boots(tmp_path):
    first = _router(tmp_path)
    first.run("011_message_search_index")
    first.model.create(name="012_channel_dm_carryover", migrated_at=datetime.utcnow())

    # A later boot: a fresh Router replays 001–012, then applies the rest.
    router = _router(tmp_path)
    router.run()

    assert router.diff == []


def test_fresh_database_runs_every_migration(tmp_path):
    router = _router(tmp_path)
    router.run()
    assert "012_channel_dm_carryover" in router.done
    assert router.diff == []
