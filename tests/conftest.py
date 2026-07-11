"""Test-suite-wide setup.

Runs before any test module imports. Pins config knobs that would otherwise
trip on the developer's local environment (real DATA_DIR, real DB, embedded
Qdrant) so unit tests run against a fully synthetic stack.

Tests that need a real DB use the ``isolated_db`` fixture from this file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# These must be set BEFORE any project module imports, since config.py reads
# them at import time and db/db.py runs migrations against the configured URL.
_TEST_DATA_DIR = Path(tempfile.gettempdir()) / "ai-assistant-tests"
_TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATA_DIR", str(_TEST_DATA_DIR))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DATA_DIR}/test.db")
os.environ.setdefault("JOB_ID", "test-bot")
os.environ.setdefault("AUTH_LOCAL_KEYS_DIR", str(_TEST_DATA_DIR / "auth_keys"))
# Disable embedded issuer side-effects during model-only tests so importing
# models.admin_users doesn't trigger any auth-flow init.
os.environ.setdefault("AUTH_LOCAL_ISSUER_ENABLED", "false")


import pytest


@pytest.fixture
def isolated_db():
    """Yields a fresh transaction that's rolled back at the end of the test.

    Use for any test that touches Peewee models. State is NOT visible across
    tests; each test sees an empty schema-loaded DB.
    """
    from db.db import DB
    from models.admin_audit_log import AdminAuditLog
    from models.admin_users import AdminUser
    from models.admin_bot_memberships import AdminBotMembership
    from models.pending_admins import PendingAdmin
    from models.local_credentials import LocalCredential

    DB.connect(reuse_if_open=True)
    # Wipe tables so each test starts clean. Can't use a transaction-rollback
    # fixture because some helpers (touch_login, link_to_idp) would happen
    # inside the rolled-back txn; simpler to delete + restart.
    for model in (AdminAuditLog, LocalCredential, AdminBotMembership, PendingAdmin, AdminUser):
        try:
            model.delete().execute()
        except Exception:  # table may not exist yet on first run
            pass
    yield DB
    for model in (AdminAuditLog, LocalCredential, AdminBotMembership, PendingAdmin, AdminUser):
        try:
            model.delete().execute()
        except Exception:
            pass
