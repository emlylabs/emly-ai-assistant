"""G13d: stored timestamps are UTC whatever the host's time zone.

The retention purge compares ``created_at`` with a UTC cut-off. Writers
that used ``datetime.now()`` stored local time and were right only while
``config.py``'s ``TZ=UTC`` pin was in force.
"""
from __future__ import annotations

import ast
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ["models", "services", "routes", "agents", "channels", "issuer", "utils", "db"]
# Display strings in alert emails, not stored timestamps.
ALLOWED = {("services/widget_email.py", 2)}


def _naive_now_calls(path: Path) -> int:
    count = 0
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if (isinstance(node, ast.Call) and not node.args and not node.keywords
                and isinstance(node.func, ast.Attribute) and node.func.attr == "now"):
            count += 1
    return count


def test_no_local_time_now_in_app_code():
    found = set()
    for top in SCANNED:
        for path in (ROOT / top).rglob("*.py"):
            n = _naive_now_calls(path)
            if n:
                found.add((str(path.relative_to(ROOT)), n))
    assert found == ALLOWED, (
        "Use utils.clock.utc_now() for stored timestamps (datetime.now() is host local time)")


@pytest.fixture
def far_from_utc():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Kiritimati"  # UTC+14
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_rows_are_stamped_in_utc_on_a_non_utc_host(isolated_db, far_from_utc):
    from models.bot_impressions import Bot_Impressions
    from models.bots import Bots
    from models.emly_users import EMLYUser, EMLYUsers

    assert datetime.now().hour != datetime.now(timezone.utc).hour  # the zone took effect
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"tz-{uuid.uuid4().hex[:6]}", name="TZ")
    vid = f"tz-{uuid.uuid4().hex[:8]}"
    EMLYUsers.insert_new_user(bot_id=bot.id, id=vid, first_name="A", last_name=None, email="",
                              phone="", ip="", browser="", meta=None)
    imp = Bot_Impressions.insert_impression(bot.id, "load")

    utc = datetime.now(timezone.utc).replace(tzinfo=None)
    user = EMLYUser.get(EMLYUser.id == vid)
    for stamp in (user.created_at, imp.created_at):
        stamp = stamp if isinstance(stamp, datetime) else datetime.fromisoformat(str(stamp))
        assert abs(stamp - utc) < timedelta(minutes=1)
