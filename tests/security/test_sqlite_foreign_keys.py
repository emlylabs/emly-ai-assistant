"""D3: SQLite enforces foreign keys on every connection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import peewee
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _fk_pragma() -> int:
    from db.db import DB

    return DB.execute_sql("PRAGMA foreign_keys").fetchone()[0]


def test_foreign_keys_on_for_main_and_new_thread_connections():
    from db.db import DATABASE_URL, DB

    if not DATABASE_URL.startswith("sqlite"):
        pytest.skip("SQLite-only pragma")
    assert _fk_pragma() == 1
    seen = []

    def worker():
        DB.connect(reuse_if_open=True)
        seen.append(_fk_pragma())
        DB.close()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen == [1]


def test_child_row_needs_existing_parent(isolated_db):
    from models.emly_users import EMLYUser

    with pytest.raises(peewee.IntegrityError):
        EMLYUser.insert(id=f"u-{uuid.uuid4().hex[:6]}", bot=f"no-such-bot-{uuid.uuid4().hex[:6]}").execute()


def test_deleting_bot_row_cascades(isolated_db):
    from models.bots import Bot, Bots
    from models.emly_messages import EMLYMessage, EMLYMessages
    from models.emly_users import EMLYUser, EMLYUsers

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"fk-{uuid.uuid4().hex[:6]}", name="FK")
    EMLYUsers.insert_new_user(bot_id=bot.id, id="visitor", first_name=None, last_name=None, email=None,
                              phone=None, ip="", browser="", meta=None)
    EMLYMessages.insert_new_message(bot_id=bot.id, user_id="visitor", session_id="s", role="user",
                                    message="hi", not_useful=False, expanded_query=None, page="p")
    Bot.delete().where(Bot.id == bot.id).execute()
    assert EMLYUser.select().where(EMLYUser.bot == bot.id).count() == 0
    assert EMLYMessage.select().where(EMLYMessage.bot == bot.id).count() == 0


_FRESH = """
import json
from db.db import DB, report_orphaned_rows
print("RESULT=" + json.dumps({
    "fk": DB.execute_sql("PRAGMA foreign_keys").fetchone()[0],
    "violations": DB.execute_sql("PRAGMA foreign_key_check").fetchall(),
    "orphans": report_orphaned_rows(),
}))
"""


def test_fresh_install_migrates_cleanly_with_enforcement(tmp_path):
    env = {**os.environ, "DATA_DIR": str(tmp_path), "DATABASE_URL": f"sqlite:///{tmp_path}/fresh.db"}
    proc = subprocess.run([sys.executable, "-c", _FRESH], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT=")), None)
    assert line, proc.stderr[-3000:]
    assert json.loads(line[len("RESULT="):]) == {"fk": 1, "violations": [], "orphans": {}}


def test_existing_orphans_are_reported_not_fatal(tmp_path):
    import sqlite3

    env = {**os.environ, "DATA_DIR": str(tmp_path), "DATABASE_URL": f"sqlite:///{tmp_path}/old.db"}
    subprocess.run([sys.executable, "-c", "import db.db"], cwd=REPO_ROOT, env=env, check=True,
                   capture_output=True, timeout=300)
    # An orphan left behind by an older build that ran without enforcement.
    con = sqlite3.connect(tmp_path / "old.db")
    con.execute("PRAGMA foreign_keys=OFF")
    # Fill NOT NULL columns with a placeholder of the right kind.
    info = con.execute("PRAGMA table_info(emly_user)").fetchall()
    row = {name: (0 if "INT" in (typ or "").upper() or "BOOL" in (typ or "").upper() else "x")
           for _, name, typ, notnull, default, _pk in info if notnull and default is None}
    row |= {"id": "ghost", "bot_id": "deleted-bot"}
    con.execute(f"INSERT INTO emly_user ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
    con.commit()
    con.close()

    proc = subprocess.run([sys.executable, "-c", _FRESH], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT=")), None)
    assert line, proc.stderr[-3000:]
    assert json.loads(line[len("RESULT="):])["orphans"] == {"emly_user": 1}
    assert "emly_user=1" in proc.stderr
