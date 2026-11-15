"""G6c: secrets can come from files (NAME_FILE), for Docker and
Kubernetes secrets, so they aren't in the container's environment."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from utils import secret_files
from utils.secret_files import SECRET_NAMES, SecretFileError, load_secret_files

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(monkeypatch):
    for name in SECRET_NAMES:
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    return monkeypatch


def test_value_is_read_from_the_file(env, tmp_path, caplog):
    f = tmp_path / "openai"
    f.write_text("sk-from-file\n")
    env.setenv("OPENAI_API_KEY_FILE", str(f))
    with caplog.at_level("INFO", logger="utils.secret_files"):
        assert load_secret_files() == ["OPENAI_API_KEY"]
    assert os.environ["OPENAI_API_KEY"] == "sk-from-file"  # trailing newline removed
    assert "OPENAI_API_KEY" in caplog.text and "sk-from-file" not in caplog.text


def test_only_the_trailing_newline_is_removed(env, tmp_path):
    f = tmp_path / "pw"
    f.write_text("  pass word \r\n")
    env.setenv("EMAIL_PASSWORD_FILE", str(f))
    load_secret_files()
    assert os.environ["EMAIL_PASSWORD"] == "  pass word "


def test_calling_twice_is_harmless(env, tmp_path):
    f = tmp_path / "k"
    f.write_text("v1")
    env.setenv("OPENAI_API_KEY_FILE", str(f))
    load_secret_files()
    assert load_secret_files() == []
    assert os.environ["OPENAI_API_KEY"] == "v1"


def test_both_set_to_different_values_stops_start_up(env, tmp_path):
    f = tmp_path / "k"
    f.write_text("from-file")
    env.setenv("OPENAI_API_KEY_FILE", str(f))
    env.setenv("OPENAI_API_KEY", "from-env")
    with pytest.raises(SecretFileError, match="Set only one") as exc:
        load_secret_files()
    assert "from-file" not in str(exc.value) and "from-env" not in str(exc.value)


@pytest.mark.parametrize("content", [None, "", "\n", "   \n"])
def test_missing_or_empty_file_stops_start_up(env, tmp_path, content):
    f = tmp_path / "k"
    if content is not None:
        f.write_text(content)
    env.setenv("OPENAI_API_KEY_FILE", str(f))
    with pytest.raises(SecretFileError, match="OPENAI_API_KEY_FILE"):
        load_secret_files()


def test_every_listed_secret_is_documented():
    sample = (REPO_ROOT / ".env.sample").read_text()
    readme = (REPO_ROOT / "README.md").read_text()
    assert "_FILE" in sample
    for name in secret_files.SECRET_NAMES:
        assert f"`{name}`" in readme[readme.index("#### Secrets from files"):], name


def test_app_start_up_reads_the_files(tmp_path):
    """The values reach the code that reads them at import time."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    (tmp_path / "bot_key").write_text(key + "\n")
    (tmp_path / "openai").write_text("sk-from-file\n")
    child_env = {k: v for k, v in os.environ.items()
                 if k not in SECRET_NAMES and not k.endswith("_FILE")}
    child_env.update(MODE="dev", DATA_DIR=str(tmp_path), DATABASE_URL=f"sqlite:///{tmp_path}/t.db",
                     AUTH_LOCAL_KEYS_DIR=str(tmp_path / "keys"),
                     BOT_SECRETS_KEY_FILE=str(tmp_path / "bot_key"),
                     OPENAI_API_KEY_FILE=str(tmp_path / "openai"))
    code = ("import main, config; from services import secrets; "
            "print('OPENAI', config.OPENAI_API_KEY == 'sk-from-file'); "
            "print('ROUNDTRIP', secrets.decrypt(secrets.encrypt('x')) == 'x'); "
            f"from cryptography.fernet import Fernet; print('KEY', Fernet({key!r}.encode()).decrypt("
            "secrets.encrypt('y').encode()) == b'y')")
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=child_env,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "OPENAI True" in proc.stdout and "ROUNDTRIP True" in proc.stdout and "KEY True" in proc.stdout
    assert "sk-from-file" not in proc.stdout + proc.stderr and key not in proc.stdout + proc.stderr
