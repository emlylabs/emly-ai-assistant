"""G13d: one clock for stored timestamps.

The database stores naive datetimes, read everywhere as UTC (the admin
UI, the retention purge, session timeouts). Some writers used
``datetime.now()``, which is the host's local time, and were only right
because ``config.py`` pins ``TZ=UTC`` for the process. A script, worker
or test that didn't import ``config`` first, on a host outside UTC,
wrote rows hours off, so the nightly purge kept or deleted them early.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Now in UTC, naive, as the database stores it."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
