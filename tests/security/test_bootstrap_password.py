"""A1: the generated bootstrap superadmin password must never reach the logs."""

from __future__ import annotations

import logging
import re
import stat

import pytest


@pytest.fixture
def bootstrap_env(monkeypatch, tmp_path, isolated_db):
    monkeypatch.setenv("AUTH_BOOTSTRAP_SUPERADMIN_EMAIL", "Boot@Example.com")
    monkeypatch.delenv("AUTH_LOCAL_BOOTSTRAP_PASSWORD", raising=False)
    monkeypatch.setenv("AUTH_LOCAL_ISSUER_ENABLED", "true")
    from models.local_credentials import LocalCredentials

    LocalCredentials.delete("bootstrap:boot@example.com")
    yield tmp_path
    LocalCredentials.delete("bootstrap:boot@example.com")


def _read_password(path) -> str:
    match = re.search(r"^temporary_password: (\S+)$", path.read_text(), re.MULTILINE)
    assert match, "password file has no temporary_password line"
    return match.group(1)


def test_generated_password_is_not_logged(bootstrap_env, caplog):
    from services.auth.bootstrap import bootstrap_password_path, bootstrap_pending_superadmin

    with caplog.at_level(logging.DEBUG):
        bootstrap_pending_superadmin(data_dir=bootstrap_env)

    path = bootstrap_password_path(bootstrap_env)
    password = _read_password(path)
    assert len(password) >= 16
    assert password not in caplog.text
    # Only the path is logged.
    assert str(path) in caplog.text


def test_password_file_is_owner_only_and_verifies(bootstrap_env):
    from models.local_credentials import LocalCredentials
    from services.auth.bootstrap import bootstrap_password_path, bootstrap_pending_superadmin
    from services.auth.issuer.passwords import verify_password

    bootstrap_pending_superadmin(data_dir=bootstrap_env)
    path = bootstrap_password_path(bootstrap_env)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    stored = LocalCredentials.get_password_hash("bootstrap:boot@example.com")
    assert verify_password(_read_password(path), stored)


def test_env_password_is_not_logged_and_no_file(bootstrap_env, monkeypatch, caplog):
    from services.auth.bootstrap import bootstrap_password_path, bootstrap_pending_superadmin

    monkeypatch.setenv("AUTH_LOCAL_BOOTSTRAP_PASSWORD", "pinned-Secret-123")  # gitleaks:allow (test fixture / not a secret)
    with caplog.at_level(logging.DEBUG):
        bootstrap_pending_superadmin(data_dir=bootstrap_env)
    assert "pinned-Secret-123" not in caplog.text
    assert not bootstrap_password_path(bootstrap_env).exists()


def test_remove_bootstrap_password_file(bootstrap_env):
    from services.auth.bootstrap import (
        bootstrap_password_path,
        bootstrap_pending_superadmin,
        remove_bootstrap_password_file,
    )

    bootstrap_pending_superadmin(data_dir=bootstrap_env)
    assert bootstrap_password_path(bootstrap_env).exists()
    remove_bootstrap_password_file(bootstrap_env)
    assert not bootstrap_password_path(bootstrap_env).exists()
    # Idempotent.
    remove_bootstrap_password_file(bootstrap_env)
