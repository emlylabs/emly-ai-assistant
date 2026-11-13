"""Shared APScheduler instance.

Persists jobs to ``{DATA_DIR}/actions.db`` so a process restart resumes
any deferred email / OTP jobs scheduled before the cutover. Started
once from ``main.py`` startup — never at module import.
"""
from __future__ import annotations

import logging

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from config import DATA_DIR, MAX_JOB_INSTANCES, PROCESS_POOL_SIZE

log = logging.getLogger(__name__)

JOBSTORE_URL = f"sqlite:///{DATA_DIR}/actions.db"
# Jobs whose arguments must never be written to disk (e.g. the plaintext
# one-time passcode handed to the OTP email job) use the in-memory store.
# They are lost on restart, which is acceptable for short-lived work.
EPHEMERAL_JOBSTORE = "memory"

jobstores = {
    "default": SQLAlchemyJobStore(url=JOBSTORE_URL),
    EPHEMERAL_JOBSTORE: MemoryJobStore(),
}
executors = {
    "default": ThreadPoolExecutor(PROCESS_POOL_SIZE),
}
job_defaults = {
    "coalesce": False,
    "max_instances": MAX_JOB_INSTANCES,
}

scheduler = BackgroundScheduler(
    jobstores=jobstores,
    executors=executors,
    job_defaults=job_defaults,
    timezone="Asia/Kolkata",
)


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.start()
    log.info("APScheduler started (jobstore=%s)", JOBSTORE_URL)
