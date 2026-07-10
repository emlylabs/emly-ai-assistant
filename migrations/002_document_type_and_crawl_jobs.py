"""Peewee migration -- 002_document_type_and_crawl_jobs.py

Two changes:

1. ``emly_files.document_type`` — a free-form-but-conventional category
   for each file (web_page / document / product / support_article / faq /
   other). Surfaced in chunk metadata so the chat surface can cite source
   context properly. Backfills existing rows to ``"document"`` since
   that's the closest match to the implicit type before this column.

2. ``crawl_jobs`` and ``crawl_job_pages`` — backend-resident state for
   the website crawler. Replaces the previous in-browser crawler that
   lost progress on tab refresh. A worker process picks up rows in
   ``running`` status on startup and resumes from where they left off.
"""
from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext  # noqa: F401


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    # ------------------------------------------------------------------
    # Document type column on emly_files
    # ------------------------------------------------------------------
    migrator.add_fields(
        "emly_files",
        document_type=pw.CharField(max_length=64, default="document"),
    )

    # ------------------------------------------------------------------
    # Crawl-job tables
    # ------------------------------------------------------------------
    @migrator.create_model
    class CrawlJob(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot_id = pw.CharField(max_length=255, index=True)
        seed_url = pw.TextField()
        # JSON-encoded CrawlerOptions (filters, caps, concurrency).
        options_json = pw.TextField()
        # running / paused / completed / cancelled / failed
        status = pw.CharField(max_length=32, default="running")
        # Aggregate counters maintained by the worker.
        pages_total = pw.IntegerField(default=0)
        pages_done = pw.IntegerField(default=0)
        pages_skipped = pw.IntegerField(default=0)
        pages_failed = pw.IntegerField(default=0)
        # 'web_page' by default — applied to every file produced by this job.
        document_type = pw.CharField(max_length=64, default="web_page")
        created_by_admin_id = pw.CharField(max_length=255, null=True)
        created_on = pw.DateTimeField()
        updated_on = pw.DateTimeField()
        completed_on = pw.DateTimeField(null=True)
        error_message = pw.TextField(null=True)

        class Meta:
            table_name = "crawl_jobs"
            indexes = (
                (("bot_id", "status"), False),
                (("bot_id", "created_on"), False),
            )

    @migrator.create_model
    class CrawlJobPage(pw.Model):
        id = pw.AutoField()
        job_id = pw.CharField(max_length=255, index=True)
        url = pw.TextField()
        depth = pw.IntegerField(default=0)
        # queued / fetching / uploading / embedding / done / skipped / failed
        state = pw.CharField(max_length=32, default="queued")
        reason = pw.TextField(null=True)
        file_id = pw.CharField(max_length=255, null=True)
        created_on = pw.DateTimeField()
        updated_on = pw.DateTimeField()

        class Meta:
            table_name = "crawl_job_pages"
            indexes = (
                (("job_id", "state"), False),
                (("job_id", "url"), True),  # dedupe within a job
            )


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_model("crawl_job_pages")
    migrator.remove_model("crawl_jobs")
    migrator.remove_fields("emly_files", "document_type")
