"""G7b: the analytics CSV is built on the server, safe to open in a
spreadsheet, and audited.

It used to be assembled in the browser with no formula guard (a topic
or channel name starting with "=" ran as a formula in Excel) and nobody
knew who had downloaded what.
"""
from __future__ import annotations

import csv
import io
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def setup(isolated_db):
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from models.emly_messages import EMLYMessage, EMLYMessages
    from routes import admin_bot_dashboard, admin_bot_sessions
    from services.auth.dependencies import get_admin

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"x-{uuid.uuid4().hex[:6]}", name="=HR bot")
    for i in range(3):
        m = EMLYMessages.insert_new_message(bot_id=bot.id, user_id="u", session_id=f"s{i}", role="assistant",
                                            message="a", not_useful=False, expanded_query=None, page="p")
        EMLYMessage.update(model_used="=cmd|' /C calc'!A0", prompt_tokens=1000, completion_tokens=500).where(
            EMLYMessage.id == m.id).execute()
    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="sa@example.com", is_active=True,
                              is_superadmin=True)
    app = FastAPI()
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.include_router(admin_bot_sessions.router, prefix="/api/admin")
    state = {"admin": admin}
    app.dependency_overrides[get_admin] = lambda: state["admin"]
    return TestClient(app), bot, state


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


def test_export_is_a_csv_of_the_page_figures(setup):
    client, bot, _ = setup
    r = client.get(f"/api/admin/bots/{bot.slug}/analytics/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith(f'attachment; filename="analytics-{bot.slug}-')
    assert r.headers["cache-control"] == "no-store"
    rows = _rows(r.text)
    sections = [row[0] for row in rows if row and row[0].startswith("# ")]
    for name in ("# KPIs", "# Latency quantiles", "# Citations", "# Resolution funnel", "# Daily messages",
                 "# Daily sessions", "# Channel mix", "# Topics", "# Enrichment"):
        assert name in sections
    kpis = {row[0]: row[1] for row in rows if len(row) == 2}
    assert kpis["total_messages"] == "3"


def test_formula_cells_are_neutralised(setup):
    client, bot, _ = setup
    rows = _rows(client.get(f"/api/admin/bots/{bot.slug}/analytics/export").text)
    (usage,) = [row for row in rows if row and "calc" in row[0]]
    assert usage[0].startswith("'=")  # a spreadsheet shows it as text
    assert usage[1:4] == ["3", "3000", "1500"]  # numbers stay numbers


def test_compare_adds_the_prior_period(setup):
    client, bot, _ = setup
    rows = _rows(client.get(f"/api/admin/bots/{bot.slug}/analytics/export?compare=true").text)
    assert ["metric", "value", "prior"] in rows


def test_export_is_audited(setup):
    from models.admin_audit_log import AdminAuditLogs

    client, bot, state = setup
    client.get(f"/api/admin/bots/{bot.slug}/analytics/export?channel_id=ch-1")
    (row,) = AdminAuditLogs.list_filtered(bot_id=bot.id, action="analytics.exported", limit=5)
    assert row.admin_id == state["admin"].id and row.payload["channel_id"] == "ch-1"


def test_non_members_cant_export(setup):
    from models.admin_users import AdminUsers

    client, bot, state = setup
    state["admin"] = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="other@example.com",
                                       is_active=True, is_superadmin=False)
    assert client.get(f"/api/admin/bots/{bot.slug}/analytics/export").status_code in (403, 404)


def test_shared_csv_cells():
    from utils.csv_export import csv_cell

    assert csv_cell("=1+1") == "'=1+1" and csv_cell("@SUM(A1)") == "'@SUM(A1)" and csv_cell("-x") == "'-x"
    assert csv_cell(-5) == -5 and csv_cell(None) == "" and csv_cell(True) == "True"
