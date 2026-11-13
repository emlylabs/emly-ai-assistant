"""Peewee migration -- 006_crawl_schedules.py

Adds the ``crawl_schedules`` table for §11 T5 (recurring crawls).

Crawl jobs to date have been one-shot — every "daily crawl of acme.com"
required an admin to manually re-trigger a new job. This table holds a
re-runnable specification (the same ``options_json`` shape the one-shot
flow uses, plus a cron-style cadence and bookkeeping fields). The
crawl-worker supervisor polls the table on each tick and spawns a fresh
``CrawlJob`` whenever ``next_run_at`` falls due.

``last_job_id`` makes the "view last run" linkback cheap; the schedule
doesn't need a per-row history because every produced ``CrawlJob`` is
already retained for 90 days under the new T7 TTL.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    @migrator.create_model
    class CrawlSchedule(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot_id = pw.CharField(max_length=255, index=True)
        seed_url = pw.TextField()
        options_json = pw.TextField()
        document_type = pw.CharField(max_length=64, default="web_page")
        # Lightweight cadence string. The supervisor parses this; we
        # don't store a parsed cron tuple because the surface is small
        # and the parser lives in services/crawl_schedule.
        cadence = pw.CharField(max_length=64)
        enabled = pw.BooleanField(default=True)
        next_run_at = pw.DateTimeField(index=True)
        last_run_at = pw.DateTimeField(null=True)
        last_job_id = pw.CharField(max_length=255, null=True)
        created_by_admin_id = pw.CharField(max_length=255, null=True)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()

        class Meta:
            table_name = "crawl_schedules"


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_model("crawl_schedules")
