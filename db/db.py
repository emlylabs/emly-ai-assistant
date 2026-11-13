import json

from peewee import Check, Database, TextField
from peewee_migrate import Router
from playhouse.db_url import connect
from config import SRC_LOG_LEVELS, DATA_DIR, DATABASE_URL, BACKEND_DIR
import os
import logging

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["DB"])


class JSONField(TextField):
    def db_value(self, value):
        return json.dumps(value)

    def python_value(self, value):
        if value is not None:
            return json.loads(value)


# Check if the file exists
if os.path.exists(f"{DATA_DIR}/ollama.db"):
    # Rename the file
    os.rename(f"{DATA_DIR}/ollama.db", f"{DATA_DIR}/webui.db")
    log.info("Database migrated from Ollama-WebUI successfully.")
else:
    pass

DB = connect(DATABASE_URL)
log.info(f"Connected to a {DB.__class__.__name__} database.")

# SQLite concurrency: the chat path holds a write transaction while
# streaming an LLM response, which can easily exceed the 5s default
# `busy_timeout` and starve concurrent admin reads with `database is
# locked` errors that surface as opaque 500s. WAL lets readers proceed
# alongside the writer, and a longer busy_timeout absorbs the rest.
# `permanent=True` re-applies on every new thread-local connection
# (peewee opens one per worker thread under FastAPI's threadpool).
if DATABASE_URL.startswith("sqlite"):
    DB.pragma("journal_mode", "wal", permanent=True)
    DB.pragma("synchronous", "normal", permanent=True)
    DB.pragma("busy_timeout", 30000, permanent=True)

router = Router(
    DB,
    migrate_dir=BACKEND_DIR / "migrations",
    logger=log,
)
router.run()

# SQLite ignores foreign keys unless asked, on every connection. Turned on
# only after migrations: SQLite applies some schema changes by copying a
# table and dropping the old one, and with enforcement on that DROP would
# fire ON DELETE CASCADE into the child tables. `permanent=True` re-applies
# it on every new thread-local connection.
if DATABASE_URL.startswith("sqlite"):
    DB.pragma("foreign_keys", 1, permanent=True)

DB.connect(reuse_if_open=True)


def report_orphaned_rows() -> dict:
    """Count rows whose parent row no longer exists (SQLite only).

    Databases from before enforcement may hold some. They stay readable,
    but updating their foreign key column now fails, so they are logged
    at boot by table. Returns ``{table: count}``."""
    if not DATABASE_URL.startswith("sqlite"):
        return {}
    counts: dict = {}
    try:
        for table, *_ in DB.execute_sql("PRAGMA foreign_key_check").fetchall():
            counts[table] = counts.get(table, 0) + 1
    except Exception:
        log.exception("foreign key check failed")
        return {}
    if counts:
        log.warning(
            "SQLite has rows whose parent row is missing (foreign keys are now enforced): %s",
            ", ".join(f"{t}={n}" for t, n in sorted(counts.items())),
        )
    return counts


report_orphaned_rows()
