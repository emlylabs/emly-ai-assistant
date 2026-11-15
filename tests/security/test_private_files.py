"""Secret files are created owner-only, never chmod-ed after the fact.

The sign-in signing key was written with the umask's permissions and
then chmod-ed to 0600, leaving a window (or, after a crash, a file) that
other local users could read. Same for the generated dev secrets key.
"""
from __future__ import annotations

import os
import stat

import pytest


@pytest.fixture
def no_chmod(monkeypatch):
    """Make any after-the-fact chmod a no-op, so only the mode a file
    was created with counts. Run under a permissive umask."""
    old = os.umask(0o022)
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    yield
    os.umask(old)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_signing_key_is_created_owner_only(tmp_path, no_chmod):
    from services.auth.issuer.keystore import Keystore

    keys = tmp_path / "auth_keys"
    Keystore(keys, key_bits=2048)
    (priv,) = [p for p in keys.glob("*.pem") if not p.name.endswith(".pub.pem")]
    assert _mode(priv) == 0o600
    assert _mode(keys) == 0o700


def test_generated_secrets_key_is_created_owner_only(tmp_path, no_chmod, monkeypatch):
    from services import secrets

    monkeypatch.delenv("BOT_SECRETS_KEY", raising=False)
    monkeypatch.setattr(secrets, "DATA_DIR", tmp_path)
    secrets._load_or_create_key()
    assert _mode(tmp_path / secrets._SECRET_FILENAME) == 0o600


def test_bootstrap_password_is_created_owner_only(tmp_path, no_chmod):
    from services.auth.bootstrap import _write_password_file

    path = tmp_path / "bootstrap_password.txt"
    _write_password_file(path, "a@corp.example", "pw")
    assert _mode(path) == 0o600 and "temporary_password: pw" in path.read_text()


def test_a_failed_write_leaves_nothing_behind(tmp_path, monkeypatch):
    from utils.private_files import write_private

    target = tmp_path / "k.pem"
    target.write_bytes(b"old")

    def boom(fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        write_private(target, b"new")
    assert target.read_bytes() == b"old"  # the previous file is untouched
    assert [p.name for p in tmp_path.iterdir()] == ["k.pem"]  # no temp file left
