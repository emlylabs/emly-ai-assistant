"""G2d/G2f: the boot log lists every outbound destination by host, and a
Qdrant outside the network is flagged.
"""
from __future__ import annotations

import logging
import uuid

import pytest

SECRETS = ("s3cret", "key=abc", "/v1", "tenant-guid", "alice")


@pytest.fixture
def deployment(isolated_db, monkeypatch):
    from models.bot_channels import BotChannel, BotChannels
    from models.bots import Bot, Bots

    env = {
        "OPENAI_BASE_URL": "https://alice:s3cret@llm.corp.example:8443/v1?key=abc",
        "QDRANT_URL": "http://qdrant:6333",
        "EMAIL_SERVER": "smtp.corp.example",
        "EMAIL_PORT": "587",
        "AUTH_OIDC_ISSUER": "https://login.microsoftonline.com/tenant-guid/v2.0",
        "APP_BASE_URL": "https://emly.corp.example",
        "EMBEDDING_PROVIDER": "openai",
        "HF_HUB_OFFLINE": "1",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ALLOW_LANGSMITH_TRACING", raising=False)

    a = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"a-{uuid.uuid4().hex[:6]}", name="A",
                    config_json={"llm": {"api_endpoint": "https://api.other.example/v1"}})
    b = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"b-{uuid.uuid4().hex[:6]}", name="B",
                    config_json={"llm": {"api_endpoint": "https://api.other.example/v2"}})
    BotChannels.insert(id=f"ch-{uuid.uuid4().hex[:6]}", bot_id=a.id, type="slack")
    BotChannels.insert(id=f"ch-{uuid.uuid4().hex[:6]}", bot_id=a.id, type="web_widget")
    off = BotChannels.insert(id=f"ch-{uuid.uuid4().hex[:6]}", bot_id=b.id, type="teams")
    BotChannels.set_active(off.id, False)
    yield
    BotChannel.delete().where(BotChannel.bot.in_([a.id, b.id])).execute()
    Bot.delete().where(Bot.id.in_([a.id, b.id])).execute()


def test_boot_lists_every_destination_by_host(deployment, caplog):
    from services.egress import egress_summary, log_egress_summary

    with caplog.at_level(logging.INFO, logger="services.egress"):
        log_egress_summary()
    text = caplog.text
    rows = dict(egress_summary())

    assert rows["Model endpoint (default)"] == "llm.corp.example:8443"
    # Other tests' bots may share the database: check this test's entry.
    assert "api.other.example (2 bots)" in rows["Model endpoints set by bots"].split(", ")
    assert rows["Embeddings"] == "llm.corp.example:8443 (remote)"
    assert rows["Vector database"] == "qdrant:6333"
    assert rows["Mail server"] == "smtp.corp.example:587"
    assert rows["Identity provider"] == "login.microsoftonline.com"
    platforms = rows["Chat platforms"].split(", ")
    assert "slack" in platforms
    assert "teams" not in platforms and "web_widget" not in platforms  # disabled; local
    assert rows["Hugging Face Hub"] == "off"
    assert rows["LangSmith tracing"] == "off"
    for _, value in rows.items():
        assert value in text
    for leaked in SECRETS:
        assert leaked not in text


def test_quiet_defaults(isolated_db, monkeypatch):
    from services.egress import egress_summary

    for k in ("OPENAI_BASE_URL", "QDRANT_URL", "EMAIL_SERVER", "AUTH_OIDC_ISSUER", "EMBEDDING_PROVIDER",
              "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.delenv(k, raising=False)
    rows = dict(egress_summary())
    assert rows["Embeddings"] == "local model, nothing sent"
    assert rows["Vector database"] == "embedded, local files"
    assert rows["Mail server"] == "off"
    assert rows["Identity provider"] == "built-in, nothing sent"
    assert rows["Hugging Face Hub"] == "on, model downloads allowed"


def test_built_in_issuer_on_own_url_is_not_external(monkeypatch):
    from services.egress import egress_summary

    monkeypatch.setenv("APP_BASE_URL", "https://emly.corp.example")
    monkeypatch.setenv("AUTH_OIDC_ISSUER", "https://emly.corp.example/")
    assert dict(egress_summary())["Identity provider"] == "built-in, nothing sent"


@pytest.mark.parametrize("url", [
    "http://qdrant:6333", "http://localhost:6333", "http://127.0.0.1:6333", "http://10.2.3.4:6333",
    "http://[::1]:6333", "http://[fd00::5]:6333", "http://qdrant.vectors.svc.cluster.local:6333",
    "https://qdrant.corp.internal", None,
])
def test_internal_qdrant_is_not_flagged(monkeypatch, url):
    from services.egress import qdrant_warnings

    if url:
        monkeypatch.setenv("QDRANT_URL", url)
    else:
        monkeypatch.delenv("QDRANT_URL", raising=False)
    assert qdrant_warnings() == []


@pytest.mark.parametrize("url,host", [
    ("https://abc-123.eu-central.aws.cloud.qdrant.io:6333", "abc-123.eu-central.aws.cloud.qdrant.io:6333"),
    ("http://8.8.8.8:6333", "8.8.8.8:6333"),
])
def test_remote_qdrant_is_flagged_on_boot_and_readyz(isolated_db, monkeypatch, caplog, url, host):
    import main
    from services.egress import log_egress_summary

    monkeypatch.setenv("QDRANT_URL", url)
    with caplog.at_level(logging.WARNING, logger="services.egress"):
        log_egress_summary()
    assert f"QDRANT_URL points at {host}" in caplog.text
    codes = [w["code"] for w in main._collect_runtime_warnings()]
    assert "qdrant_remote" in codes


# ------------------------------------------------------------- G13e
def test_sslmode_in_the_url_reaches_the_driver():
    """DATABASE_URL query options are passed to psycopg2 unchanged, so
    TLS to Postgres is configured in the URL."""
    from playhouse.db_url import connect

    db = connect("postgresql://u:p@db.example.com:5432/emly?sslmode=verify-full&sslrootcert=/run/ca.pem")
    assert db.connect_params["sslmode"] == "verify-full"
    assert db.connect_params["sslrootcert"] == "/run/ca.pem"


@pytest.mark.parametrize("mode,url,warned", [
    ("prod", "postgresql://u:p@db.example.com/emly", True),
    ("prod", "postgresql://u:p@db.example.com/emly?sslmode=prefer", True),
    ("prod", "postgresql://u:p@db.example.com/emly?sslmode=require", False),
    ("prod", "postgresql://u:p@db.example.com/emly?sslmode=verify-full", False),
    ("prod", "postgresql://u:p@postgres:5432/emly", False),      # compose service name
    ("prod", "postgresql://u:p@10.0.3.4/emly", False),
    ("prod", "sqlite:////data/app.db", False),
    ("dev", "postgresql://u:p@db.example.com/emly", False),
])
def test_remote_postgres_without_tls_is_flagged_in_prod(monkeypatch, mode, url, warned):
    from services.egress import postgres_tls_warnings

    monkeypatch.setenv("MODE", mode)
    monkeypatch.setenv("DATABASE_URL", url)
    warnings = postgres_tls_warnings()
    assert bool(warnings) is warned
    if warnings:
        assert warnings[0]["code"] == "postgres_no_tls"
        assert "u:p" not in warnings[0]["message"] and "db.example.com" in warnings[0]["message"]
