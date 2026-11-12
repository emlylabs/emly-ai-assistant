"""Q4: recurring jobs must be storable in the persistent jobstore.

The invite-expiry sweep used to be a nested function, which APScheduler's
SQLAlchemy jobstore can't pickle, so ``add_job`` raised on every boot and
the sweep never ran.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = """
import json, sqlite3, os
import main
from services.scheduler import scheduler
scheduler.start(paused=True)
main._register_invite_expiry_sweep()
main._register_audit_retention_purge()
main._register_data_retention_purge()
jobs = {j.id: f"{j.func.__module__}:{j.func.__name__}" for j in scheduler.get_jobs(jobstore="default")}
rows = [r[0] for r in sqlite3.connect(os.path.join(os.environ["DATA_DIR"], "actions.db"))
        .execute("select id from apscheduler_jobs")]
scheduler.shutdown(wait=False)
print("RESULT=" + json.dumps({"jobs": jobs, "rows": sorted(rows)}))
"""


def test_recurring_jobs_are_persisted(tmp_path):
    env = {
        **os.environ,
        "DATA_DIR": str(tmp_path),
        "DATABASE_URL": f"sqlite:///{tmp_path}/probe.db",
        "AUTH_BOOTSTRAP_SUPERADMIN_EMAIL": "",
    }
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300
    )
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT=")), None)
    assert line, f"probe failed:\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-4000:]}"
    result = json.loads(line[len("RESULT="):])
    assert result["jobs"] == {
        "invite_expiry_sweep": "services.invite_email:sweep_expired_invites",
        "audit_retention_purge": "services.audit:purge_expired_audit_rows",
        "data_retention_purge": "services.data_retention:purge_expired_data",
    }
    assert result["rows"] == ["audit_retention_purge", "data_retention_purge", "invite_expiry_sweep"]
    assert "cannot be serialized" not in proc.stderr


def test_sweep_marks_only_past_invites_expired():
    from models.bots import Bots
    from models.pending_admins import STATUS_EXPIRED, STATUS_PENDING, PendingAdmins
    from services.invite_email import sweep_expired_invites

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"s-{uuid.uuid4().hex[:6]}", name="S")
    past = PendingAdmins.create_space_invite(
        email=f"old-{uuid.uuid4().hex[:6]}@example.com", invited_by=None, target_bot_id=bot.id,
        role="viewer", expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
    )
    future = PendingAdmins.create_space_invite(
        email=f"new-{uuid.uuid4().hex[:6]}@example.com", invited_by=None, target_bot_id=bot.id,
        role="viewer", expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
    )

    assert sweep_expired_invites() >= 1
    assert PendingAdmins.get_by_id(past.id).status == STATUS_EXPIRED
    assert PendingAdmins.get_by_id(future.id).status == STATUS_PENDING
    assert sweep_expired_invites() == 0  # idempotent
