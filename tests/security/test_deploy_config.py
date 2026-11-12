"""Deployment-config guards (A11, A12, A14).

These read the files as text/YAML so a later edit can't quietly reopen a
port, restore a default password or drop a telemetry switch.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())


# -------------------------------------------------------------------- A11


def test_qdrant_ports_not_published():
    qdrant = _compose()["services"]["qdrant"]
    assert not qdrant.get("ports"), "Qdrant must stay on the internal compose network"


def test_qdrant_telemetry_disabled():
    env = _compose()["services"]["qdrant"].get("environment") or {}
    assert str(env.get("QDRANT__TELEMETRY_DISABLED")).lower() == "true"


def test_postgres_password_has_no_default():
    text = (REPO_ROOT / "docker-compose.yml").read_text()
    uses = re.findall(r"\$\{POSTGRES_PASSWORD[^}]*\}", text)
    assert uses, "compose should reference POSTGRES_PASSWORD"
    for use in uses:
        assert use.startswith("${POSTGRES_PASSWORD:?"), f"must be required, not defaulted: {use}"
    assert "emlypassword" not in text


def test_env_sample_has_no_postgres_password():
    sample = (REPO_ROOT / ".env.sample").read_text()
    assert re.search(r"^POSTGRES_PASSWORD=\s*$", sample, re.MULTILINE)
    assert "emlypassword" not in sample


# -------------------------------------------------------------------- A12


def test_entrypoint_execs_so_signals_reach_uvicorn():
    job = (REPO_ROOT / "job.sh").read_text()
    start = (REPO_ROOT / "start.sh").read_text()
    assert re.search(r"^exec \./start\.sh\b", job, re.MULTILINE)
    assert "&" not in re.sub(r"&&", "", job), "job.sh must not background start.sh"
    assert re.search(r"^exec uvicorn\b", start, re.MULTILINE)


def test_dockerfile_creates_runtime_user_once():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert len(re.findall(r"\buseradd\b", dockerfile)) == 1


# -------------------------------------------------------------------- A13


def test_env_sample_has_no_unused_admin_vars():
    sample = (REPO_ROOT / ".env.sample").read_text()
    for name in ("ADMIN_EMAIL", "ADMIN_PASSWORD", "ADMIN_JWT_SECRET"):
        assert not re.search(rf"^#?\s*{name}=", sample, re.MULTILINE), name


# -------------------------------------------------------------------- A14


def test_next_telemetry_disabled_in_ui_builder():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    stage = dockerfile.split("AS ui-builder", 1)[1].split("\nFROM ", 1)[0]
    assert re.search(r"^ENV NEXT_TELEMETRY_DISABLED=1\b", stage, re.MULTILINE)


# --------------------------------------------------------------------- B5


def test_start_sh_does_not_trust_every_proxy():
    start = (REPO_ROOT / "start.sh").read_text()
    assert "--forwarded-allow-ips '*'" not in start
    assert '--forwarded-allow-ips "*"' not in start
    assert re.search(r'^FORWARDED_ALLOW_IPS="\$\{FORWARDED_ALLOW_IPS:-127\.0\.0\.1\}"', start, re.MULTILINE)
    assert '--forwarded-allow-ips "$FORWARDED_ALLOW_IPS"' in start


# --------------------------------------------------------------------- C8


def test_rotation_script_is_not_dockerignored():
    ignore = (REPO_ROOT / ".dockerignore").read_text().splitlines()
    assert "!scripts/rotate_bot_secrets.py" in ignore
    assert "!scripts/__init__.py" in ignore
