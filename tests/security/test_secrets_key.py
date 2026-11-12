"""C8: BOT_SECRETS_KEY required in production; rotation with
BOT_SECRETS_KEY_PREVIOUS + MultiFernet + scripts.rotate_bot_secrets."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

REPO_ROOT = Path(__file__).resolve().parents[2]
KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
KEY_C = Fernet.generate_key().decode()


@pytest.fixture
def clean_secret_columns(isolated_db):
    """The test DB is shared across tests; rows other tests left behind may
    hold values encrypted under other keys. Start from a clean slate."""
    from models.bot_channels import BotChannel
    from models.bots import Bot

    Bot.update(api_key_encrypted=None).execute()
    BotChannel.update(credentials_encrypted=None).execute()


@pytest.fixture
def keys(monkeypatch):
    from services import secrets

    def use(current: str, previous: str | None = None):
        monkeypatch.setenv("BOT_SECRETS_KEY", current)
        if previous is None:
            monkeypatch.delenv("BOT_SECRETS_KEY_PREVIOUS", raising=False)
        else:
            monkeypatch.setenv("BOT_SECRETS_KEY_PREVIOUS", previous)
        secrets.reset_for_tests()

    yield use
    secrets.reset_for_tests()


# ------------------------------------------------------------ boot check


def test_prod_requires_key(monkeypatch, tmp_path):
    from services import secrets

    monkeypatch.setenv("MODE", "prod")
    monkeypatch.delenv("BOT_SECRETS_KEY", raising=False)
    monkeypatch.setattr(secrets, "DATA_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="MODE=prod requires BOT_SECRETS_KEY"):
        secrets.validate_configuration()


def test_prod_error_points_at_existing_generated_key(monkeypatch, tmp_path):
    from services import secrets

    (tmp_path / ".bot_secrets_key").write_text(KEY_A)
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.delenv("BOT_SECRETS_KEY", raising=False)
    monkeypatch.setattr(secrets, "DATA_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="set BOT_SECRETS_KEY to its contents"):
        secrets.validate_configuration()


def test_dev_without_key_is_allowed(monkeypatch):
    from services import secrets

    monkeypatch.setenv("MODE", "dev")
    monkeypatch.delenv("BOT_SECRETS_KEY", raising=False)
    secrets.validate_configuration()


@pytest.mark.parametrize("name", ["BOT_SECRETS_KEY", "BOT_SECRETS_KEY_PREVIOUS"])
def test_malformed_keys_fail_fast(monkeypatch, name):
    from services import secrets

    monkeypatch.setenv("BOT_SECRETS_KEY", KEY_A)
    monkeypatch.setenv(name, "not-a-fernet-key")
    with pytest.raises(RuntimeError, match=f"{name} is not a valid Fernet key"):
        secrets.validate_configuration()


def test_prod_app_refuses_to_boot_without_key(tmp_path):
    env = {**os.environ, "MODE": "prod", "APP_BASE_URL": "https://emly.example.com",
           "DATA_DIR": str(tmp_path), "DATABASE_URL": f"sqlite:///{tmp_path}/k.db",
           "AUTH_LOCAL_ISSUER_ENABLED": "false", "BOT_SECRETS_KEY": ""}
    proc = subprocess.run([sys.executable, "-c", "import main"], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode != 0
    assert "MODE=prod requires BOT_SECRETS_KEY" in proc.stderr


# --------------------------------------------------------------- rotation


def test_old_key_still_decrypts_until_reencrypt(keys):
    from services import secrets

    keys(KEY_A)
    old_ciphertext = secrets.encrypt("sk-123")
    keys(KEY_B, previous=KEY_A)
    assert secrets.decrypt(old_ciphertext) == "sk-123"
    # New writes use the new key only.
    assert Fernet(KEY_B.encode()).decrypt(secrets.encrypt("x").encode()) == b"x"
    keys(KEY_B)  # previous removed before re-encrypting → old value unreadable
    with pytest.raises(InvalidToken):
        secrets.decrypt(old_ciphertext)


def test_rotate_script_reencrypts_everything(keys, clean_secret_columns):
    from models.bot_channels import BotChannel, BotChannels
    from models.bots import Bot, Bots
    from scripts.rotate_bot_secrets import main, rotate_all
    from services import secrets

    keys(KEY_A)
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"r-{uuid.uuid4().hex[:6]}", name="R")
    Bot.update(api_key_encrypted=secrets.encrypt("sk-bot")).where(Bot.id == bot.id).execute()
    ch = BotChannels.insert(id=f"ch-{uuid.uuid4().hex[:8]}", bot_id=bot.id, type="telegram",
                            credentials_encrypted=secrets.encrypt('{"t":"1"}'))

    keys(KEY_B, previous=KEY_A)
    dry = rotate_all(dry_run=True)
    assert dry == {"rotated": 2, "failed": []}
    assert main([]) == 0

    keys(KEY_B)  # old key gone: everything must now be readable with B alone
    assert secrets.decrypt(Bot.get_by_id(bot.id).api_key_encrypted) == "sk-bot"
    assert secrets.decrypt(BotChannel.get_by_id(ch.id).credentials_encrypted) == '{"t":"1"}'
    BotChannel.delete().where(BotChannel.id == ch.id).execute()
    Bot.delete().where(Bot.id == bot.id).execute()


def test_rotate_script_reports_unreadable_values(keys, clean_secret_columns):
    from models.bots import Bot, Bots
    from scripts.rotate_bot_secrets import main

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"u-{uuid.uuid4().hex[:6]}", name="U")
    Bot.update(api_key_encrypted=Fernet(KEY_C.encode()).encrypt(b"x").decode()).where(Bot.id == bot.id).execute()
    keys(KEY_B, previous=KEY_A)
    assert main([]) == 1
    Bot.delete().where(Bot.id == bot.id).execute()


def test_widget_tokens_survive_rotation(keys):
    from services.auth.widget_hmac import WidgetTokenError, issue, verify

    keys(KEY_A)
    token = issue(bot_id="bot-1", user_id="u", session_id="s", key_version=1)
    keys(KEY_B, previous=KEY_A)
    assert verify(token, expected_bot_id="bot-1", current_key_version=1).user_id == "u"
    keys(KEY_B)
    with pytest.raises(WidgetTokenError, match="bad_signature"):
        verify(token, expected_bot_id="bot-1", current_key_version=1)
