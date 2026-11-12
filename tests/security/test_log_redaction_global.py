"""Q1: secret redaction must apply to records from *every* logger.

Previously the filter was attached to the root logger, which Python only
consults for records created on root itself — records from ``routes.*``,
``services.*`` etc. reached the handlers unredacted.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "eyJhbGciOiJSUzI1NiJ9.payload.sig"


@pytest.fixture
def global_redaction():
    from services.auth.logging_filter import install_global

    original = logging.getLogRecordFactory()
    install_global()
    yield
    logging.setLogRecordFactory(original)


@pytest.fixture
def root_capture():
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(self.format(record))

    handler = _Capture()
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    yield captured
    root.removeHandler(handler)
    root.setLevel(previous_level)


def test_child_logger_secret_redacted_at_root_handler(global_redaction, root_capture):
    logging.getLogger("routes.some_module").info("Outbound: Authorization: Bearer %s", TOKEN)
    assert root_capture, "record did not reach the root handler"
    assert TOKEN not in root_capture[-1]
    assert "Bearer <redacted>" in root_capture[-1]


def test_secret_split_across_msg_and_args_is_redacted(global_redaction, root_capture):
    logging.getLogger("services.x").warning("token response %s", {"access_token": "s3cr3t-value"})
    assert "s3cr3t-value" not in root_capture[-1]


def test_records_without_secrets_keep_their_args(global_redaction):
    record = logging.getLogger("services.y").makeRecord(
        "services.y", logging.INFO, __file__, 1, "count=%d", (3,), None
    )
    assert record.args == (3,)
    assert record.getMessage() == "count=3"


def test_access_log_args_preserved_for_uvicorn_formatter(global_redaction):
    from uvicorn.logging import AccessFormatter

    invite = "qSHuX3TQGkHZZ36R7jGw_ZYRPVFOU54Dl6Vwa1h72iw"
    record = logging.getLogger("uvicorn.access").makeRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:5555", "GET", f"/api/admin/admins/invites/{invite}", "1.1", 200), None,
    )
    assert isinstance(record.args, tuple) and len(record.args) == 5
    line = AccessFormatter(fmt="%(request_line)s %(status_code)s", use_colors=False).format(record)
    assert invite not in line
    assert "/invites/<redacted>" in line


def test_install_global_is_idempotent(global_redaction):
    from services.auth.logging_filter import install_global

    factory = logging.getLogRecordFactory()
    install_global()
    assert logging.getLogRecordFactory() is factory


def test_caplog_sees_redacted_text(global_redaction, caplog):
    with caplog.at_level(logging.INFO):
        logging.getLogger("channels.slack").info("Cookie: emly_admin_session=abc.def.ghi")
    assert "abc.def.ghi" not in caplog.text


_PROBE = f"""
import logging, main
logging.getLogger("routes.probe").warning("Authorization: Bearer {TOKEN}")
"""


def test_main_installs_global_redaction(tmp_path):
    """End-to-end: after importing the real app, an app-module log line
    carrying a bearer token is redacted on stderr."""
    env = {
        **os.environ,
        "MODE": "dev",
        "DATA_DIR": str(tmp_path),
        "DATABASE_URL": f"sqlite:///{tmp_path}/probe.db",
        "AUTH_LOCAL_ISSUER_ENABLED": "false",
        "AUTH_BOOTSTRAP_SUPERADMIN_EMAIL": "",
    }
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    out = proc.stdout + proc.stderr
    assert "Bearer <redacted>" in out
    assert TOKEN not in out
