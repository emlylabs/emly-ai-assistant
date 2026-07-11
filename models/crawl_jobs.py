"""Persistent state for backend website-crawl jobs.

The previous design ran the crawler in the admin browser and lost
progress on tab refresh. This module tracks the work in two tables so a
worker process can resume any in-progress job after a restart.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import List, Optional

import peewee as pw

from db.db import DB

# Job status values.
JOB_STATUS_RUNNING = "running"
JOB_STATUS_PAUSED = "paused"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_CANCELLED = "cancelled"
JOB_STATUS_FAILED = "failed"
JOB_TERMINAL_STATUSES = {JOB_STATUS_COMPLETED, JOB_STATUS_CANCELLED, JOB_STATUS_FAILED}

# Per-page state values.
PAGE_QUEUED = "queued"
PAGE_FETCHING = "fetching"
PAGE_UPLOADING = "uploading"
PAGE_EMBEDDING = "embedding"
PAGE_DONE = "done"
PAGE_SKIPPED = "skipped"
PAGE_FAILED = "failed"


class CrawlJob(pw.Model):
    id = pw.CharField(max_length=255, primary_key=True)
    bot_id = pw.CharField(max_length=255, index=True)
    seed_url = pw.TextField()
    options_json = pw.TextField()
    status = pw.CharField(max_length=32, default=JOB_STATUS_RUNNING)
    pages_total = pw.IntegerField(default=0)
    pages_done = pw.IntegerField(default=0)
    pages_skipped = pw.IntegerField(default=0)
    pages_failed = pw.IntegerField(default=0)
    document_type = pw.CharField(max_length=64, default="web_page")
    created_by_admin_id = pw.CharField(max_length=255, null=True)
    created_on = pw.DateTimeField()
    updated_on = pw.DateTimeField()
    completed_on = pw.DateTimeField(null=True)
    error_message = pw.TextField(null=True)

    class Meta:
        database = DB
        table_name = "crawl_jobs"


class CrawlJobPage(pw.Model):
    id = pw.AutoField()
    job_id = pw.CharField(max_length=255, index=True)
    url = pw.TextField()
    depth = pw.IntegerField(default=0)
    state = pw.CharField(max_length=32, default=PAGE_QUEUED)
    reason = pw.TextField(null=True)
    file_id = pw.CharField(max_length=255, null=True)
    created_on = pw.DateTimeField()
    updated_on = pw.DateTimeField()

    class Meta:
        database = DB
        table_name = "crawl_job_pages"


def _job_to_dict(row: CrawlJob) -> dict:
    return {
        "id": row.id,
        "bot_id": row.bot_id,
        "seed_url": row.seed_url,
        "options": json.loads(row.options_json) if row.options_json else {},
        "status": row.status,
        "pages_total": row.pages_total,
        "pages_done": row.pages_done,
        "pages_skipped": row.pages_skipped,
        "pages_failed": row.pages_failed,
        "document_type": row.document_type,
        "created_by_admin_id": row.created_by_admin_id,
        "created_on": row.created_on.isoformat() if row.created_on else None,
        "updated_on": row.updated_on.isoformat() if row.updated_on else None,
        "completed_on": row.completed_on.isoformat() if row.completed_on else None,
        "error_message": row.error_message,
    }


def _page_to_dict(row: CrawlJobPage) -> dict:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "url": row.url,
        "depth": row.depth,
        "state": row.state,
        "reason": row.reason,
        "file_id": row.file_id,
        "created_on": row.created_on.isoformat() if row.created_on else None,
        "updated_on": row.updated_on.isoformat() if row.updated_on else None,
    }


class CrawlJobsTable:
    def __init__(self, db: pw.Database) -> None:
        self.db = db
        self.db.create_tables([CrawlJob, CrawlJobPage])

    # -- Jobs ----------------------------------------------------------
    def create(
        self,
        bot_id: str,
        seed_url: str,
        options: dict,
        document_type: str,
        created_by_admin_id: Optional[str],
    ) -> dict:
        now = datetime.now()
        row = CrawlJob.create(
            id=str(uuid.uuid4()),
            bot_id=bot_id,
            seed_url=seed_url,
            options_json=json.dumps(options),
            status=JOB_STATUS_RUNNING,
            document_type=document_type,
            created_by_admin_id=created_by_admin_id,
            created_on=now,
            updated_on=now,
        )
        return _job_to_dict(row)

    def get(self, bot_id: str, job_id: str) -> Optional[dict]:
        try:
            row = CrawlJob.get((CrawlJob.id == job_id) & (CrawlJob.bot_id == bot_id))
            return _job_to_dict(row)
        except pw.DoesNotExist:
            return None

    def get_any(self, job_id: str) -> Optional[dict]:
        try:
            return _job_to_dict(CrawlJob.get(CrawlJob.id == job_id))
        except pw.DoesNotExist:
            return None

    def list_for_bot(self, bot_id: str, limit: int = 50) -> List[dict]:
        rows = (
            CrawlJob.select()
            .where(CrawlJob.bot_id == bot_id)
            .order_by(CrawlJob.created_on.desc())
            .limit(limit)
        )
        return [_job_to_dict(r) for r in rows]

    def list_running(self) -> List[dict]:
        rows = CrawlJob.select().where(CrawlJob.status == JOB_STATUS_RUNNING)
        return [_job_to_dict(r) for r in rows]

    def update_status(
        self,
        job_id: str,
        new_status: str,
        error_message: Optional[str] = None,
        completed: bool = False,
    ) -> bool:
        now = datetime.now()
        updates: dict = {"status": new_status, "updated_on": now}
        if error_message is not None:
            updates["error_message"] = error_message
        if completed or new_status in JOB_TERMINAL_STATUSES:
            updates["completed_on"] = now
        return CrawlJob.update(**updates).where(CrawlJob.id == job_id).execute() > 0

    def bump_counters(
        self,
        job_id: str,
        delta_total: int = 0,
        delta_done: int = 0,
        delta_skipped: int = 0,
        delta_failed: int = 0,
    ) -> None:
        # SQL-level increments to avoid read-modify-write races between workers.
        updates: dict = {"updated_on": datetime.now()}
        if delta_total:
            updates["pages_total"] = CrawlJob.pages_total + delta_total
        if delta_done:
            updates["pages_done"] = CrawlJob.pages_done + delta_done
        if delta_skipped:
            updates["pages_skipped"] = CrawlJob.pages_skipped + delta_skipped
        if delta_failed:
            updates["pages_failed"] = CrawlJob.pages_failed + delta_failed
        if updates:
            CrawlJob.update(**updates).where(CrawlJob.id == job_id).execute()

    # -- Pages ---------------------------------------------------------
    def add_page_if_new(self, job_id: str, url: str, depth: int) -> bool:
        """Insert a (job_id, url) row if it doesn't exist. Returns True if added."""
        if (
            CrawlJobPage.select()
            .where((CrawlJobPage.job_id == job_id) & (CrawlJobPage.url == url))
            .exists()
        ):
            return False
        now = datetime.now()
        CrawlJobPage.create(
            job_id=job_id,
            url=url,
            depth=depth,
            state=PAGE_QUEUED,
            created_on=now,
            updated_on=now,
        )
        return True

    def update_page_state(
        self,
        page_id: int,
        state: str,
        reason: Optional[str] = None,
        file_id: Optional[str] = None,
    ) -> bool:
        updates: dict = {"state": state, "updated_on": datetime.now()}
        if reason is not None:
            updates["reason"] = reason
        if file_id is not None:
            updates["file_id"] = file_id
        return CrawlJobPage.update(**updates).where(CrawlJobPage.id == page_id).execute() > 0

    def claim_next_queued(self, job_id: str) -> Optional[dict]:
        """Atomically grab the next queued page and mark it fetching.

        Uses a row-level update guarded by the previous state so two workers
        can't claim the same page even if they run simultaneously.
        """
        candidate = (
            CrawlJobPage.select()
            .where((CrawlJobPage.job_id == job_id) & (CrawlJobPage.state == PAGE_QUEUED))
            .order_by(CrawlJobPage.id.asc())
            .first()
        )
        if candidate is None:
            return None
        rows = (
            CrawlJobPage.update(state=PAGE_FETCHING, updated_on=datetime.now())
            .where((CrawlJobPage.id == candidate.id) & (CrawlJobPage.state == PAGE_QUEUED))
            .execute()
        )
        if rows == 0:
            return None
        return _page_to_dict(CrawlJobPage.get(CrawlJobPage.id == candidate.id))

    def list_pages(
        self,
        job_id: str,
        limit: int = 200,
        offset: int = 0,
        states: Optional[List[str]] = None,
    ) -> List[dict]:
        q = CrawlJobPage.select().where(CrawlJobPage.job_id == job_id)
        if states:
            q = q.where(CrawlJobPage.state.in_(states))
        q = q.order_by(CrawlJobPage.id.asc()).limit(limit).offset(offset)
        return [_page_to_dict(r) for r in q]

    def count_pages(self, job_id: str, state: Optional[str] = None) -> int:
        q = CrawlJobPage.select().where(CrawlJobPage.job_id == job_id)
        if state:
            q = q.where(CrawlJobPage.state == state)
        return q.count()

    def reset_in_flight_for_job(self, job_id: str) -> int:
        """On worker startup, any page that was claimed but never finished
        (state in fetching/uploading/embedding) gets pushed back to queued.
        Crash recovery — without this, restarting the pod would orphan
        those pages forever."""
        rows = (
            CrawlJobPage.update(state=PAGE_QUEUED, updated_on=datetime.now())
            .where(
                (CrawlJobPage.job_id == job_id)
                & CrawlJobPage.state.in_([PAGE_FETCHING, PAGE_UPLOADING, PAGE_EMBEDDING])
            )
            .execute()
        )
        return rows

    def has_pages_in_flight(self, job_id: str) -> bool:
        return (
            CrawlJobPage.select()
            .where(
                (CrawlJobPage.job_id == job_id)
                & CrawlJobPage.state.in_([PAGE_QUEUED, PAGE_FETCHING, PAGE_UPLOADING, PAGE_EMBEDDING])
            )
            .exists()
        )

    def list_visited_urls(self, job_id: str) -> set[str]:
        rows = CrawlJobPage.select(CrawlJobPage.url).where(CrawlJobPage.job_id == job_id)
        return {r.url for r in rows}


Crawl_Jobs = CrawlJobsTable(DB)
