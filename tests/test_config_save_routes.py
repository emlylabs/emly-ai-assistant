"""Config write routes: format-rule blocking, the force override, and
schema errors mapped to 422 on every write path (live, draft, restore)."""
from __future__ import annotations

import sys
import types
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BAD_COLOUR = {"widget": {"theme": {"header_background": "nope"}}}


@pytest.fixture
def client(isolated_db, monkeypatch):
    from models.admin_users import AdminUsers
    from routes import admin_bot_dashboard, admin_bot_sessions
    from services.auth.dependencies import get_admin

    # Runtime invalidation would build the real agent stack; not under test.
    stub = types.ModuleType("utils.dependencies")
    stub.AGENT_SERVICE_INSTANCE = types.SimpleNamespace(invalidate_bot=lambda _bot_id: None)
    monkeypatch.setitem(sys.modules, "utils.dependencies", stub)

    app = FastAPI()
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.include_router(admin_bot_sessions.router, prefix="/api/admin")
    admin = AdminUsers.insert(
        id=f"adm-{uuid.uuid4().hex[:6]}", email="super@example.com", is_active=True, is_superadmin=True,
    )
    app.dependency_overrides[get_admin] = lambda: admin
    return TestClient(app)


@pytest.fixture
def bot(isolated_db):
    from models.bots import Bots

    bid = f"bot-{uuid.uuid4().hex[:8]}"
    return Bots.insert(id=bid, slug=f"s-{bid}", name="B")


@pytest.fixture
def gate_on(monkeypatch):
    """Workspace save gate on, and a runtime error (RT1: no API key
    anywhere) so the gate has something forceable to block on."""
    from models import workspace_settings
    from routes import admin_bot_dashboard
    from services.config_validation import ValidationContext

    monkeypatch.setattr(
        workspace_settings.WorkspaceSettingsStore, "get",
        lambda: types.SimpleNamespace(settings={"validation": {"block_save_on_errors": True}}),
    )
    monkeypatch.setattr(
        admin_bot_dashboard, "_build_validation_context",
        lambda _bot: ValidationContext(has_bot_api_key=False, has_deployment_api_key=False),
    )


def _forced_audits(bot_id):
    from models.admin_audit_log import AdminAuditLog

    return AdminAuditLog.select().where(
        (AdminAuditLog.bot_id == bot_id) & (AdminAuditLog.action == "config.save.forced")
    ).count()


def test_format_error_blocks_save_without_force_offer(client, bot):
    r = client.put(f"/api/admin/bots/{bot.slug}/config", json={"config": BAD_COLOUR})
    assert r.status_code == 422
    assert r.json()["detail"]["can_force"] is False


def test_named_colour_saves(client, bot):
    r = client.put(
        f"/api/admin/bots/{bot.slug}/config",
        json={"config": {"widget": {"theme": {"header_background": "white"}}}},
    )
    assert r.status_code == 200, r.text


def test_gate_does_not_offer_force_for_format_errors(client, bot, gate_on):
    r = client.put(f"/api/admin/bots/{bot.slug}/config", json={"config": BAD_COLOUR})
    assert r.status_code == 422
    assert r.json()["detail"]["can_force"] is False


def test_gate_offers_force_for_forceable_errors(client, bot, gate_on):
    r = client.put(f"/api/admin/bots/{bot.slug}/config", json={"config": {}})
    assert r.status_code == 422
    assert r.json()["detail"]["can_force"] is True

    r = client.put(f"/api/admin/bots/{bot.slug}/config?force=1", json={"config": {}})
    assert r.status_code == 200, r.text
    assert _forced_audits(bot.id) == 1


def test_forcing_a_format_error_is_not_audited_as_forced(client, bot, gate_on):
    r = client.put(f"/api/admin/bots/{bot.slug}/config?force=1", json={"config": BAD_COLOUR})
    assert r.status_code == 422
    assert r.json()["detail"]["can_force"] is False
    assert _forced_audits(bot.id) == 0


def test_schema_error_is_422_on_live_save(client, bot):
    r = client.put(f"/api/admin/bots/{bot.slug}/config", json={"config": {"llm": {"model": {"not": "a string"}}}})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "invalid_config"


def test_schema_error_is_422_on_draft_save(client, bot):
    r = client.put(f"/api/admin/bots/{bot.slug}/config/draft", json={"config": {"llm": {"model": {"not": "a string"}}}})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "invalid_config"


def test_draft_may_hold_format_errors_until_publish(client, bot):
    """Drafts can be broken; publish is where they're blocked."""
    r = client.put(f"/api/admin/bots/{bot.slug}/config/draft", json={"config": BAD_COLOUR})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/admin/bots/{bot.slug}/config/draft/publish")
    assert r.status_code == 422


def test_history_restore_applies_format_rules(client, bot):
    from models.bot_config_history import BotConfigHistories

    entry = BotConfigHistories.snapshot(bot_id=bot.id, config_version=1, config_json=BAD_COLOUR)
    r = client.post(f"/api/admin/bots/{bot.slug}/config/history/{entry.id}/restore")
    assert r.status_code == 422
    assert r.json()["detail"]["can_force"] is False


def test_conversation_search_needs_two_characters(client, bot):
    assert client.get(f"/api/admin/bots/{bot.slug}/sessions?q=a").status_code == 422
    assert client.get(f"/api/admin/bots/{bot.slug}/sessions?q=ab").status_code == 200
