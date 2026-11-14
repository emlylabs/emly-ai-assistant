"""G2c: an operator's tracing variable can't silently turn LangSmith on.

F4 (test_langchain_runtime.py) proves tracing is off by default and that
the app never turns it on. This covers the remaining hole: compose passes
``.env`` straight into the container, so a leftover
``LANGCHAIN_TRACING_V2=true`` used to send every prompt and reply to
LangSmith's cloud.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from utils import tracing_guard

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(monkeypatch):
    for name in (*tracing_guard.TRACING_SWITCHES, tracing_guard.OPT_IN_ENV):
        monkeypatch.delenv(name, raising=False)
    from langsmith import utils

    utils.get_env_var.cache_clear()
    yield monkeypatch
    utils.get_env_var.cache_clear()


@pytest.mark.parametrize("switch", tracing_guard.TRACING_SWITCHES)
def test_any_tracing_switch_is_removed(env, switch, caplog):
    from langsmith import utils

    env.setenv(switch, "true")
    assert utils.tracing_is_enabled() is True  # read and cached before the guard

    assert tracing_guard.disable_unapproved_tracing() == [switch]

    assert switch not in os.environ
    assert utils.tracing_is_enabled() is False
    assert switch in caplog.text and "Tracing is OFF" in caplog.text


def test_opt_in_keeps_tracing(env):
    from langsmith import utils

    env.setenv("LANGCHAIN_TRACING_V2", "true")
    env.setenv(tracing_guard.OPT_IN_ENV, "true")
    assert tracing_guard.disable_unapproved_tracing() == []
    assert utils.tracing_is_enabled() is True


def test_nothing_set_is_quiet(env, caplog):
    assert tracing_guard.disable_unapproved_tracing() == []
    assert "LangSmith" not in caplog.text


def test_app_boot_turns_an_env_switch_off(tmp_path):
    child_env = {k: v for k, v in os.environ.items() if k not in tracing_guard.TRACING_SWITCHES}
    child_env.pop(tracing_guard.OPT_IN_ENV, None)
    # Own data dir and DB: the parent test process holds the shared
    # embedded Qdrant lock.
    child_env.update(LANGCHAIN_TRACING_V2="true", LANGSMITH_TRACING="true",
                     DATA_DIR=str(tmp_path), DATABASE_URL=f"sqlite:///{tmp_path}/t.db",
                     AUTH_LOCAL_KEYS_DIR=str(tmp_path / "keys"))
    code = "import main; from langsmith import utils; print('TRACING', utils.tracing_is_enabled())"
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=child_env,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "TRACING False" in proc.stdout


def test_image_defaults_tracing_off():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "LANGSMITH_TRACING=false" in dockerfile
    assert "LANGCHAIN_TRACING_V2=false" in dockerfile
