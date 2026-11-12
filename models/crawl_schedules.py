"""Persistent state for recurring crawl schedules.

§11 T5. A ``CrawlSchedule`` is a re-runnable spec: the same ``options``
shape used by one-shot ``CrawlJob`` creation, plus a cadence and
bookkeeping fields. The crawl-worker supervisor polls due rows and
spawns fresh ``CrawlJob`` instances.

Cadence format is intentionally minimal (no full crontab) — the surface
is small enough that admins want presets, and a full crontab parser is
a yak to shave. Supported values:

    hourly
    daily
    weekly
    every:<N>m       — minutes (>=5)
    every:<N>h       — hours
    every:<N>d       — days
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

import peewee as pw

from db.db import DB


# Friendly preset names that map onto ``every:<N>h`` / ``every:<N>d``.
PRESETS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}

_EVERY_RE = re.compile(r"^every:(\d+)([mhd])$")
_MIN_MINUTES = 5  # Stop runaway crawl loops.


def parse_cadence(cadence: str) -> timedelta:
    """Return the interval represented by ``cadence``.

    Raises ``ValueError`` on unknown / out-of-bounds values. Callers
    should surface that as a 400 to the admin rather than silently
    falling back to a default.
    """
    cadence = (cadence or "").strip().lower()
    if cadence in PRESETS:
        return PRESETS[cadence]
    m = _EVERY_RE.match(cadence)
    if m is None:
        raise ValueError(
            f"Unrecognized cadence {cadence!r}. Use hourly, daily, weekly, "
            "or every:<N>{m|h|d}"
        )
    n, unit = int(m.group(1)), m.group(2)
    if unit == "m":
        if n < _MIN_MINUTES:
            raise ValueError(f"Minimum cadence is {_MIN_MINUTES} minutes")
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(days=n)


class CrawlSchedule(pw.Model):
    id = pw.CharField(max_length=255, primary_key=True)
    bot_id = pw.CharField(max_length=255, index=True)
    seed_url = pw.TextField()
    options_json = pw.TextField()
    document_type = pw.CharField(max_length=64, default="web_page")
    cadence = pw.CharField(max_length=64)
    enabled = pw.BooleanField(default=True)
    next_run_at = pw.DateTimeField(index=True)
    last_run_at = pw.DateTimeField(null=True)
    last_job_id = pw.CharField(max_length=255, null=True)
    created_by_admin_id = pw.CharField(max_length=255, null=True)
    created_at = pw.DateTimeField()
    updated_at = pw.DateTimeField()

    class Meta:
        database = DB
        table_name = "crawl_schedules"


def _to_dict(row: CrawlSchedule) -> dict:
    return {
        "id": row.id,
        "bot_id": row.bot_id,
        "seed_url": row.seed_url,
        "options": json.loads(row.options_json) if row.options_json else {},
        "document_type": row.document_type,
        "cadence": row.cadence,
        "enabled": bool(row.enabled),
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "last_job_id": row.last_job_id,
        "created_by_admin_id": row.created_by_admin_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class CrawlSchedulesTable:
    def __init__(self, db: pw.Database) -> None:
        self.db = db
        self.db.create_tables([CrawlSchedule])

    def create(
        self,
        bot_id: str,
        seed_url: str,
        options: dict,
        document_type: str,
        cadence: str,
        created_by_admin_id: Optional[str],
        run_immediately: bool = True,
    ) -> dict:
        # Validate cadence up-front so a bad value never lands in the DB.
        interval = parse_cadence(cadence)
        now = datetime.now()
        # Default behaviour: first run kicks off right away so admins
        # don't have to wait a day to see whether the schedule works.
        # ``run_immediately=False`` defers to one cadence interval out.
        first_run = now if run_immediately else now + interval
        row = CrawlSchedule.create(
            id=str(uuid.uuid4()),
            bot_id=bot_id,
            seed_url=seed_url,
            options_json=json.dumps(options),
            document_type=document_type,
            cadence=cadence,
            enabled=True,
            next_run_at=first_run,
            created_by_admin_id=created_by_admin_id,
            created_at=now,
            updated_at=now,
        )
        return _to_dict(row)

    def list_for_bot(self, bot_id: str) -> List[dict]:
        rows = (
            CrawlSchedule.select()
            .where(CrawlSchedule.bot_id == bot_id)
            .order_by(CrawlSchedule.created_at.desc())
        )
        return [_to_dict(r) for r in rows]

    def get(self, bot_id: str, schedule_id: str) -> Optional[dict]:
        try:
            row = CrawlSchedule.get(
                (CrawlSchedule.id == schedule_id) & (CrawlSchedule.bot_id == bot_id)
            )
            return _to_dict(row)
        except pw.DoesNotExist:
            return None

    def due(self, now: datetime) -> List[dict]:
        """Schedules whose ``next_run_at`` has passed and which are enabled."""
        rows = (
            CrawlSchedule.select()
            .where(
                (CrawlSchedule.enabled == True)  # noqa: E712 — peewee idiom
                & (CrawlSchedule.next_run_at <= now)
            )
            .order_by(CrawlSchedule.next_run_at.asc())
        )
        return [_to_dict(r) for r in rows]

    def mark_run(self, schedule_id: str, job_id: str, now: datetime) -> bool:
        # Read-modify-write so we recompute next_run_at against the
        # *configured* cadence rather than walking from the prior next.
        try:
            row = CrawlSchedule.get(CrawlSchedule.id == schedule_id)
        except pw.DoesNotExist:
            return False
        try:
            interval = parse_cadence(row.cadence)
        except ValueError:
            # Don't strand a schedule with a busted cadence; bump it a
            # day out and let the admin fix it.
            interval = timedelta(days=1)
        CrawlSchedule.update(
            last_run_at=now,
            last_job_id=job_id,
            next_run_at=now + interval,
            updated_at=now,
        ).where(CrawlSchedule.id == schedule_id).execute()
        return True

    def set_enabled(self, bot_id: str, schedule_id: str, enabled: bool) -> bool:
        rows = (
            CrawlSchedule.update(enabled=enabled, updated_at=datetime.now())
            .where(
                (CrawlSchedule.id == schedule_id) & (CrawlSchedule.bot_id == bot_id)
            )
            .execute()
        )
        return rows > 0

    def delete(self, bot_id: str, schedule_id: str) -> bool:
        rows = (
            CrawlSchedule.delete()
            .where(
                (CrawlSchedule.id == schedule_id) & (CrawlSchedule.bot_id == bot_id)
            )
            .execute()
        )
        return rows > 0


Crawl_Schedules = CrawlSchedulesTable(DB)
