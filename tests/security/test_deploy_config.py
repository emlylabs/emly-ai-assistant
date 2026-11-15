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


def test_container_port_is_pinned_whatever_port_env_says():
    """PORT in .env chooses the host port of the compose mapping, but
    ``env_file: .env`` also passed it into the container, where start.sh
    listened on it: with PORT=18080 the app listened on 18080 while the
    mapping and healthcheck used 8080, so it was unreachable (found in the
    G8b restore drill)."""
    app = _compose()["services"]["app"]
    assert app["environment"]["PORT"] == "8080"
    assert any(str(p).endswith(":8080") for p in app["ports"])


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


# ------------------------------------------------------------------ E1, E2


def _dockerfile() -> str:
    return (REPO_ROOT / "Dockerfile").read_text()


def test_widget_installs_from_committed_lockfile():
    import json

    lock = json.loads((REPO_ROOT / "widget" / "package-lock.json").read_text())
    pkg = json.loads((REPO_ROOT / "widget" / "package.json").read_text())
    assert lock["lockfileVersion"] >= 3
    root = lock["packages"][""]
    assert root.get("dependencies") == pkg["dependencies"]
    assert root.get("devDependencies") == pkg["devDependencies"]
    for path, entry in lock["packages"].items():
        if path and not entry.get("link"):
            assert entry.get("resolved", "").startswith("https://registry.npmjs.org/"), path
            assert entry.get("integrity", "").startswith("sha512-"), path
    # Instructions only (comments may mention `npm install`); a RUN can
    # continue onto the next line.
    code = "\n".join(l for l in _dockerfile().splitlines() if not l.lstrip().startswith("#"))
    assert "npm install" not in code
    assert code.count("npm ci") == 2  # widget and admin UI


def test_base_images_pinned_by_digest():
    text = _dockerfile()
    froms = re.findall(r"^FROM\s+(\S+)", text, flags=re.M)
    assert froms, "no FROM lines"
    for image in froms:
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"not pinned by digest: {image}"
    for image in re.findall(r"COPY\s+--from=(\S+:\S+)", text):  # external images only
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"not pinned by digest: {image}"
    assert "pip install" not in text
    syntax = text.splitlines()[0]
    assert re.match(r"# syntax=docker/dockerfile:[\d.]+@sha256:[0-9a-f]{64}$", syntax), syntax
    assert "imagetools inspect" in text  # update instructions present


def test_runtime_image_drops_system_pip():
    """F6: the base image's pip is unused (uv owns /app/.venv) and its
    vendored urllib3/msgpack/setuptools trail their fixes."""
    runtime = _dockerfile().split(" AS runtime", 1)[1]
    code = [l for l in runtime.splitlines() if l.startswith("RUN")]
    assert "RUN python -m pip uninstall --yes --no-input pip" in code


# ---------------------------------------------------------------------- E3


def _workflows() -> dict:
    return {p.name: p.read_text() for p in (REPO_ROOT / ".github" / "workflows").glob("*.yml")}


def test_ci_runs_required_checks():
    ci = _workflows()["ci.yml"]
    for needle in ("uv run pytest", "pip-audit", "docker build", "--severity CRITICAL", "--exit-code 1",
                   "gitleaks git", "fetch-depth: 0"):
        assert needle in ci, needle
    on = yaml.safe_load(ci)[True]  # YAML 1.1 reads the bare `on` key as True
    assert "pull_request" in on and "push" in on
    release = _workflows()["release.yml"]
    assert "syft" in release and "spdx-json" in release and "gh release upload" in release


def test_pip_audit_blocks_the_build():
    # Q8: the runtime dependencies audit clean, so a new advisory must fail CI.
    job = yaml.safe_load(_workflows()["ci.yml"])["jobs"]["pip-audit"]
    assert not job.get("continue-on-error"), "pip-audit must fail the build on a known advisory"
    audit = next(step["run"] for step in job["steps"] if step.get("name") == "Audit")
    assert 'exit "${status:-0}"' in audit  # the audit's exit status reaches the job


def test_workflow_dependencies_are_pinned():
    for name, text in _workflows().items():
        for ref in re.findall(r"uses:\s*(\S+)", text):
            assert re.search(r"@[0-9a-f]{40}$", ref), f"{name}: action not pinned to a commit: {ref}"
        for image in re.findall(r"(?:TRIVY|SYFT)_IMAGE:\s*(\S+)", text):
            assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"{name}: image not pinned: {image}"
        assert "permissions:" in text, f"{name}: set least-privilege permissions"
    assert "sha256sum -c" in _workflows()["ci.yml"]  # gitleaks binary verified


# ---------------------------------------------------------------------- E4


def test_security_policy_present():
    text = (REPO_ROOT / "SECURITY.md").read_text()
    assert "security@emlylabs.com" in text
    for heading in ("## Reporting a vulnerability", "## What to expect", "## Supported versions"):
        assert heading in text, heading
    assert "business days" in text


# ---------------------------------------------------------------------- E5

# Every environment variable added or changed by the security work.
HARDENING_VARS = (
    "MODE", "APP_BASE_URL", "PUBLIC_BASE_URL", "BOT_SECRETS_KEY", "BOT_SECRETS_KEY_PREVIOUS",
    "POSTGRES_PASSWORD", "FORWARDED_ALLOW_IPS", "AUTH_LOCAL_ISSUER_ENABLED", "AUTH_LOCAL_MIN_PASSWORD_LENGTH",
    "AUTH_BOOTSTRAP_SUPERADMIN_EMAIL", "AUTH_LOCAL_BOOTSTRAP_PASSWORD", "AUTH_OIDC_REQUEST_OFFLINE_ACCESS",
    "AUTH_OIDC_EMAIL_CLAIMS", "AUTH_OIDC_TRUST_EMAIL_UNCONDITIONALLY", "AUTH_COOKIE_SECURE",
    "AUTH_COOKIE_SAMESITE", "BOT_CREATION", "ENABLE_API_DOCS", "WIDGET_TOKEN_TTL_SECONDS",
    "WIDGET_TOKEN_RENEWAL_WINDOW_SECONDS", "WIDGET_TOKEN_ROTATION_GRACE_SECONDS", "AUTH_RATE_",
    "EMAIL_SERVER", "MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
    "SENTENCE_TRANSFORMERS_HOME", "RE_RANKING_MODEL_HOME", "RAG_EMBEDDING_MODEL_REVISION",
    "RE_RANKING_MODEL_REVISION", "RAG_EMBEDDING_TRUST_REMOTE_CODE", "AUDIT_RETENTION_DAYS",
    "WEBHOOK_DEDUPE_RETENTION_HOURS", "LLM_ENDPOINT_ALLOWLIST", "ALLOW_LANGSMITH_TRACING", "WIDGET_MAX_REQUEST_MB", "AUDIT_FAIL_CLOSED",
    "CRAWL_ALLOWED_PRIVATE_CIDRS", "WIDGET_ICON_HOSTS", "BOT_SECRETS_KEY_FILE",
    "REQUIRE_PER_BOT_LLM_KEY", "AUTH_OIDC_REQUIRED_AMR",
    "AUTH_SESSION_MAX_AGE_SECONDS", "AUDIT_DATA_ACCESS", "AUTH_ALLOW_BEARER", "AUDIT_LOG_STDOUT",
)


def _readme() -> str:
    return (REPO_ROOT / "README.md").read_text()


def test_production_hardening_lists_every_variable():
    text = _readme()
    section = text[text.index("## Production hardening"):text.index("## Configuration Reference")]
    missing = [v for v in HARDENING_VARS if f"`{v}" not in section]
    assert missing == []


def test_readme_internal_links_resolve():
    text = _readme()

    def anchor(heading: str) -> str:
        return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")

    anchors = {anchor(h) for h in re.findall(r"^#{1,6}\s+(.+)$", text, flags=re.M)}
    for target in re.findall(r"\]\(#([^)]+)\)", text):
        assert target in anchors, f"broken anchor #{target}"
    for target in re.findall(r"\]\((?!https?:|#|mailto:)([^)#]+)", text):
        assert (REPO_ROOT / target).exists(), f"broken link {target}"


def _minor(version: str) -> tuple:
    major, minor = version.lstrip("v").split("-")[0].split(".")[:2]
    return int(major), int(minor)


def test_qdrant_server_matches_the_client():
    """qdrant-client 1.17 against server 1.12 logged an incompatibility
    warning on every start (found in the G8a cluster trial). Qdrant
    supports one minor version of difference."""
    import re

    lock = (REPO_ROOT / "uv.lock").read_text()
    client = re.search(r'name = "qdrant-client"\nversion = "([^"]+)"', lock).group(1)
    compose_tag = _compose()["services"]["qdrant"]["image"].split(":")[1]
    k8s = yaml.safe_load((REPO_ROOT / "deploy/k8s/base/kustomization.yaml").read_text())
    (k8s_tag,) = [i["newTag"] for i in k8s["images"] if i["name"] == "qdrant"]
    assert compose_tag.split("-")[0] == k8s_tag.split("-")[0], "compose and Kubernetes run different Qdrant versions"
    (c_major, c_minor), (s_major, s_minor) = _minor(client), _minor(compose_tag)
    assert c_major == s_major and abs(c_minor - s_minor) <= 1, (client, compose_tag)
