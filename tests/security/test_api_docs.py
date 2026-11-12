"""A5: API docs off in production."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ A5 docs


@pytest.mark.parametrize(
    "mode, override, expected",
    [
        ("dev", None, True),
        ("", None, True),
        ("prod", None, False),
        ("production", None, False),
        ("prod", "true", True),
        ("dev", "false", False),
    ],
)
def test_api_docs_enabled_matrix(monkeypatch, mode, override, expected):
    from utils.runtime import api_docs_enabled

    monkeypatch.setenv("MODE", mode)
    if override is None:
        monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    else:
        monkeypatch.setenv("ENABLE_API_DOCS", override)
    assert api_docs_enabled() is expected


def test_fastapi_docs_kwargs_disable_routes(monkeypatch):
    from utils.runtime import fastapi_docs_kwargs

    monkeypatch.setenv("MODE", "prod")
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    client = TestClient(FastAPI(**fastapi_docs_kwargs()))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path

    monkeypatch.setenv("ENABLE_API_DOCS", "true")
    client = TestClient(FastAPI(**fastapi_docs_kwargs()))
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


_PROBE = """
import json
from fastapi.testclient import TestClient
import main
client = TestClient(main.app)
print("RESULT=" + json.dumps({p: client.get(p, follow_redirects=False).status_code
                             for p in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")}))
"""


def test_full_app_docs_404_in_prod(tmp_path):
    """End-to-end: the real app with MODE=prod must 404 the doc paths
    instead of serving them or falling through to the UI catch-all."""
    env = {
        **os.environ,
        "MODE": "prod",
        "APP_BASE_URL": "https://emly.example.com",
        "DATA_DIR": str(tmp_path),
        "DATABASE_URL": f"sqlite:///{tmp_path}/probe.db",
        "AUTH_LOCAL_ISSUER_ENABLED": "false",
        "AUTH_BOOTSTRAP_SUPERADMIN_EMAIL": "",
        "BOT_SECRETS_KEY": os.environ.get("BOT_SECRETS_KEY", ""),
    }
    env.pop("ENABLE_API_DOCS", None)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300
    )
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT=")), None)
    assert line, f"probe failed:\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-4000:]}"
    statuses = json.loads(line[len("RESULT="):])
    assert statuses == {p: 404 for p in statuses}, statuses
