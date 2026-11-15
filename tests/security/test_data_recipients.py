"""G2g: each bot's Settings page lists where its data goes."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _uid(p: str) -> str:
    return f"{p}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def env(monkeypatch):
    for name in ("EMBEDDING_PROVIDER", "QDRANT_URL", "ALLOW_LANGSMITH_TRACING", "LLM_ENDPOINT_ALLOWLIST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.internal/v1")
    monkeypatch.setenv("MODEL", "default-model")
    monkeypatch.setenv("EMAIL_SERVER", "smtp.example.com")
    return monkeypatch


def _bot(**config):
    from models.bots import Bots

    return Bots.insert(id=_uid("bot"), slug=_uid("dr"), name="DR", config_json=config or None)


def _by_what(rows):
    return {r["what"]: r for r in rows}


def test_default_bot_lists_deployment_endpoint_and_local_pieces(isolated_db, env):
    from services.data_recipients import for_bot

    rows = _by_what(for_bot(_bot().id))
    model = rows["Answers, alert summaries and classification"]
    assert model["destination"] == "llm.internal"
    assert model["scope"] == "internal"
    assert "default-model" in model["detail"] and "deployment default" in model["detail"]
    assert rows["Document search (embeddings)"]["destination"] == "this server"
    assert rows["Search index (passages of your documents)"]["destination"] == "this server"
    assert "Form alert emails" not in rows  # no form has recipients
    assert not any("LangSmith" in w for w in rows)


def test_bot_endpoint_channels_alerts_and_remote_services(isolated_db, env):
    from models.bot_channels import BotChannels
    from services.data_recipients import for_bot

    env.setenv("EMBEDDING_PROVIDER", "openai")
    env.setenv("QDRANT_URL", "https://abc.cloud.qdrant.io:6333")
    env.setenv("ALLOW_LANGSMITH_TRACING", "true")
    bot = _bot(
        llm={"api_endpoint": "https://api.openai.com/v1?key=secret", "model": "gpt-4o-mini"},
        alert_email_content="full",
        c_forms_selected=[
            {"Contact": {"form_schema": {"id": "contact"},
                         "trigger": {"alert_emails": ["hr@example.com", "HR@example.com"]}}},
            {"Leave": {"form_schema": {"id": "leave"}, "trigger": {"alert_emails": "ops@example.com"}}},
        ],
    )
    BotChannels.insert(id=_uid("ch"), bot_id=bot.id, type="slack", display_name="HR workspace")
    BotChannels.insert(id=_uid("ch"), bot_id=bot.id, type="web_widget")

    rows = _by_what(for_bot(bot.id))
    model = rows["Answers, alert summaries and classification"]
    assert model["destination"] == "api.openai.com"  # host only: no path or query
    assert model["scope"] == "external" and "set on this bot" in model["detail"]
    assert rows["Document search (embeddings)"]["destination"] == "llm.internal"
    assert rows["Search index (passages of your documents)"]["scope"] == "external"
    slack = rows["Slack (HR workspace)"]
    assert slack["destination"] == "slack.com" and slack["scope"] == "external"
    assert not any(w.startswith("web_widget") for w in rows)
    alerts = rows["Form alert emails"]
    assert alerts["destination"] == "hr@example.com, ops@example.com"
    assert "smtp.example.com" in alerts["detail"]
    assert "transcript" in alerts["detail"]
    assert rows["LangSmith tracing"]["destination"] == "api.smith.langchain.com"
    assert "secret" not in repr(rows)


def test_alerts_with_email_off_and_blocked_endpoint(isolated_db, env):
    from services.data_recipients import for_bot

    env.delenv("EMAIL_SERVER")
    env.setenv("LLM_ENDPOINT_ALLOWLIST", "llm.internal")
    bot = _bot(llm={"api_endpoint": "https://api.openai.com/v1"},
               c_forms_selected=[{"C": {"trigger": {"alert_emails": ["a@example.com"]}}}])
    rows = _by_what(for_bot(bot.id))
    assert rows["Answers, alert summaries and classification"]["destination"] == "none"
    assert "LLM_ENDPOINT_ALLOWLIST" in rows["Answers, alert summaries and classification"]["detail"]
    assert rows["Form alert emails"]["destination"] == "none"


def test_route_is_member_only(isolated_db, env):
    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers
    from routes import admin_bot_dashboard
    from services.auth.dependencies import get_admin

    bot = _bot()
    admin = AdminUsers.insert(id=_uid("adm"), email=f"{_uid('a')}@example.com", is_active=True,
                              is_superadmin=False)
    app = FastAPI()
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: admin
    client = TestClient(app)
    url = f"/api/admin/bots/{bot.slug}/data-recipients"

    assert client.get(url).status_code == 403
    AdminBotMemberships.grant(id=_uid("m"), admin_id=admin.id, bot_id=bot.id, role="viewer")
    r = client.get(url)
    assert r.status_code == 200
    assert r.json()[0]["destination"] == "llm.internal"
