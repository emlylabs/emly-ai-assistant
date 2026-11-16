"""G2b: remote embeddings never default to api.openai.com.

With EMBEDDING_PROVIDER=openai every document chunk and search query goes
to the embedding endpoint. OPENAI_BASE_URL used to be optional, and when
it was unset the OpenAI client silently sent everything to
api.openai.com.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from services import llm_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_unset_endpoint_is_refused(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="requires OPENAI_BASE_URL"):
        llm_settings.embedding_endpoint()


def test_endpoint_off_the_allowlist_is_refused(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_ENDPOINT_ALLOWLIST", "llm.internal")
    with pytest.raises(RuntimeError, match="api.openai.com is not on LLM_ENDPOINT_ALLOWLIST"):
        llm_settings.embedding_endpoint()


def test_allowed_endpoint_is_used_and_logged_by_host(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.internal/v1")
    monkeypatch.setenv("LLM_ENDPOINT_ALLOWLIST", "llm.internal")
    assert llm_settings.embedding_endpoint() == "https://llm.internal/v1"
    assert "every document chunk and search query is sent to llm.internal" in caplog.text


def test_app_config_refuses_to_load_without_an_endpoint():
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_BASE_URL", "LLM_ENDPOINT_ALLOWLIST")}
    env.update(EMBEDDING_PROVIDER="openai", OPENAI_API_KEY="k")
    proc = subprocess.run([sys.executable, "-c", "import config"], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0
    assert "EMBEDDING_PROVIDER=openai requires OPENAI_BASE_URL" in proc.stderr
