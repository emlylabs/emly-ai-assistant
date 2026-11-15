"""G7a: spend uses a workspace price table, not a list in the browser.

The analytics page priced a handful of OpenAI model names with a table
hard-coded in the UI, so Azure, OpenRouter and self-hosted models showed
no cost. Prices are now a superadmin-edited workspace setting, and the
API returns each model's cost.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.model_prices import InvalidModelPrices, PriceTable, validate


def test_cost_is_per_million_tokens():
    t = PriceTable({"gpt-4o-mini": (0.15, 0.6)})
    assert t.cost_usd("gpt-4o-mini", 1_000_000, 500_000) == pytest.approx(0.45)


@pytest.mark.parametrize("model", ["gpt-4o-mini", "GPT-4o-mini", "openai/gpt-4o-mini"])
def test_names_match_exactly_or_by_last_segment(model):
    assert PriceTable({"gpt-4o-mini": (1.0, 2.0)}).price(model) == (1.0, 2.0)
    assert PriceTable({"openai/gpt-4o-mini": (1.0, 2.0)}).price("gpt-4o-mini") == (1.0, 2.0)


def test_unpriced_model_has_no_cost_and_zero_is_free():
    t = PriceTable({"my-llama": (0.0, 0.0)})
    assert t.cost_usd("other", 10, 10) is None
    assert t.cost_usd("my-llama", 10, 10) == 0.0


@pytest.mark.parametrize("bad", [
    [], {"m": 1}, {"m": {"input_per_1m": -1, "output_per_1m": 1}},
    {"m": {"input_per_1m": "1", "output_per_1m": 1}}, {"m": {"input_per_1m": 1}},
    {"m": {"input_per_1m": 1, "output_per_1m": 1, "extra": 2}}, {"": {"input_per_1m": 1, "output_per_1m": 1}},
    {"m": {"input_per_1m": float("inf"), "output_per_1m": 1}},
])
def test_malformed_tables_are_refused(bad):
    with pytest.raises(InvalidModelPrices):
        validate(bad)


@pytest.fixture
def client(isolated_db):
    from models.admin_users import AdminUsers
    from routes import admin_bot_dashboard, workspace
    from services.auth.dependencies import get_admin

    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="sa@example.com", is_active=True,
                              is_superadmin=True)
    app = FastAPI()
    app.include_router(workspace.router, prefix="/api/admin")
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: admin
    yield TestClient(app)
    from models.workspace_settings import WorkspaceSettingsStore

    WorkspaceSettingsStore.put({}, admin_id=admin.id)


def test_by_model_returns_cost_from_the_workspace_table(client):
    from models.bots import Bots
    from models.emly_messages import EMLYMessage, EMLYMessages

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"p-{uuid.uuid4().hex[:6]}", name="P")
    for model in ("azure-deploy-1", "self-hosted"):
        m = EMLYMessages.insert_new_message(bot_id=bot.id, user_id="u", session_id="s", role="assistant",
                                            message="a", not_useful=False, expanded_query=None, page="p")
        EMLYMessage.update(model_used=model, prompt_tokens=2_000_000, completion_tokens=1_000_000).where(
            EMLYMessage.id == m.id).execute()

    r = client.put("/api/admin/workspace/settings", json={"settings": {"model_prices": {
        "azure-deploy-1": {"input_per_1m": 2.5, "output_per_1m": 10}}}})
    assert r.status_code == 200, r.text
    rows = {row["model"]: row for row in client.get(f"/api/admin/bots/{bot.slug}/messages/by-model").json()}
    assert rows["azure-deploy-1"]["cost_usd"] == pytest.approx(15.0)
    assert rows["self-hosted"]["cost_usd"] is None  # no price: unknown, not $0


def test_a_bad_table_cant_be_saved(client):
    r = client.put("/api/admin/workspace/settings",
                   json={"settings": {"model_prices": {"m": {"input_per_1m": -2, "output_per_1m": 1}}}})
    assert r.status_code == 400 and r.json()["detail"]["key"] == "model_prices"


def test_the_ui_has_no_hard_coded_prices():
    from pathlib import Path

    ui = Path(__file__).resolve().parents[1] / "ui"
    for f in list((ui / "components").rglob("*.tsx")) + list((ui / "app").rglob("*.tsx")):
        assert "PRICE_TABLE" not in f.read_text(), f
