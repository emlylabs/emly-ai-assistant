"""Q-G13b: new bots keep conversations for 365 days unless told otherwise.

Existing bots had no value (keep everything) and keep it: the default is
written into a new bot's config at creation, not into the schema.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(isolated_db):
    from models.admin_users import AdminUsers
    from routes import admin_bots
    from services.auth.dependencies import get_admin

    app = FastAPI()
    app.include_router(admin_bots.router, prefix="/api/admin")
    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="ret@example.com", is_active=True,
                              is_superadmin=True)
    app.dependency_overrides[get_admin] = lambda: admin
    return TestClient(app)


def _create(client, **extra):
    from services.bot_config import get_config_for_bot
    from models.bots import Bots

    slug = f"ret-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/admin/bots", json={"slug": slug, "name": "R", **extra})
    assert r.status_code == 201, r.text
    return get_config_for_bot(Bots.get_by_slug(slug).id).limits.retention_days


def _template(config_json):
    from models.workspace_bot_templates import WorkspaceBotTemplates

    tid = f"wt-{uuid.uuid4()}"
    WorkspaceBotTemplates.insert(id=tid, name="t", description=None, config_json=config_json,
                                 created_by_admin_id=None)
    return f"workspace:{tid}"


def test_blank_and_builtin_template_bots_get_a_year(client):
    from services.bot_templates import list_templates

    assert _create(client) == 365
    for tpl in list_templates():
        assert _create(client, template_id=tpl.id) == 365, tpl.id


def test_workspace_template_choice_wins(client):
    assert _create(client, template_id=_template({})) == 365
    assert _create(client, template_id=_template({"limits": {"retention_days": 30}})) == 30
    assert _create(client, template_id=_template({"limits": {"retention_days": None}})) is None


def test_duplicate_keeps_the_source_setting_and_existing_bots_are_unchanged(client):
    from models.bots import Bots
    from services.bot_config import get_config_for_bot

    old = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"old-{uuid.uuid4().hex[:6]}", name="Old",
                      config_json={})
    assert get_config_for_bot(old.id).limits.retention_days is None
    assert _create(client, source_bot_id=old.id) is None
