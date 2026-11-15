"""G1d/N4: every path that creates a bot config runs the config-save checks.

Workspace templates, duplicates and built-in templates used to store
``config_json`` unvalidated. ``BotConfig`` forbids unknown keys and
``boot_validate_all_configs`` re-raised, so one bad config stopped the
app from starting (N4 now reports it instead). Duplicates and workspace-template bots always hit
this: they were stamped with a legacy ``agent_config`` block.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BAD_COLOUR = {"widget": {"theme": {"header_background": "nope"}}}


@pytest.fixture(autouse=True)
def _drop_created_rows(isolated_db):
    """``isolated_db`` doesn't wipe bots or templates. The legacy rows
    these tests insert can't load, so a later test that boots ``main``
    would report them as broken bots."""
    from models.bots import Bot
    from models.workspace_bot_templates import WorkspaceBotTemplate

    bots_before = {b.id for b in Bot.select(Bot.id)}
    templates_before = {t.id for t in WorkspaceBotTemplate.select(WorkspaceBotTemplate.id)}
    yield
    Bot.delete().where(Bot.id.not_in(bots_before or {""})).execute()
    WorkspaceBotTemplate.delete().where(
        WorkspaceBotTemplate.id.not_in(templates_before or {""})
    ).execute()


@pytest.fixture
def client(isolated_db):
    from models.admin_users import AdminUsers
    from routes import admin_bots, workspace
    from services.auth.dependencies import get_admin

    app = FastAPI()
    app.include_router(admin_bots.router, prefix="/api/admin")
    app.include_router(workspace.router, prefix="/api/admin")
    admin = AdminUsers.insert(
        id=f"adm-{uuid.uuid4().hex[:6]}", email="super@example.com", is_active=True, is_superadmin=True,
    )
    app.dependency_overrides[get_admin] = lambda: admin
    return TestClient(app)


def _bot(config_json):
    from models.bots import Bots

    bid = f"bot-{uuid.uuid4().hex[:8]}"
    return Bots.insert(id=bid, slug=f"s-{bid}", name="Source", config_json=config_json)


def _template(config_json):
    from models.workspace_bot_templates import WorkspaceBotTemplates

    tid = f"wt-{uuid.uuid4()}"
    WorkspaceBotTemplates.insert(
        id=tid, name="legacy", description=None, config_json=config_json, created_by_admin_id=None,
    )
    return tid


def _template_count():
    from models.workspace_bot_templates import WorkspaceBotTemplates

    return len(WorkspaceBotTemplates.list())


def _create_bot(client, **extra):
    slug = f"new-{uuid.uuid4().hex[:6]}"
    return slug, client.post("/api/admin/bots", json={"slug": slug, "name": "New", **extra})


# -- workspace template creation ------------------------------------------------
def test_template_with_unknown_key_is_rejected(client):
    before = _template_count()
    r = client.post("/api/admin/workspace/templates", json={"name": "t", "config_json": {"enrichment": True, "bogus": 1}})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "invalid_config"
    assert _template_count() == before


def test_template_with_bad_format_value_is_rejected(client):
    r = client.post("/api/admin/workspace/templates", json={"name": "t", "config_json": BAD_COLOUR})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "validation_failed"


def test_valid_template_is_saved(client):
    r = client.post("/api/admin/workspace/templates", json={"name": "t", "config_json": {"enrichment_enabled": True}})
    assert r.status_code == 201, r.text


def test_snapshot_drops_legacy_agent_config(client):
    src = _bot({"agent_config": {"bot_slug": "old"}, "enrichment_enabled": True})
    r = client.post("/api/admin/workspace/templates", json={"name": "t", "source_bot_slug": src.slug})
    assert r.status_code == 201, r.text
    assert "agent_config" not in r.json()["config_json"]


def test_snapshot_keeps_a_bad_value_already_stored_on_the_source(client):
    """A value stored before a format rule existed doesn't block a copy,
    matching the config editor."""
    src = _bot(BAD_COLOUR)
    r = client.post("/api/admin/workspace/templates", json={"name": "t", "source_bot_slug": src.slug})
    assert r.status_code == 201, r.text


# -- bot creation ---------------------------------------------------------------
def test_bot_from_template_with_unknown_key_is_rejected(client):
    from models.bots import Bots

    tid = _template({"bogus": 1})
    slug, r = _create_bot(client, template_id=f"workspace:{tid}")
    assert r.status_code == 422
    assert Bots.get_by_slug(slug) is None


def test_bot_from_template_with_bad_format_value_is_rejected(client):
    tid = _template(BAD_COLOUR)
    _, r = _create_bot(client, template_id=f"workspace:{tid}")
    assert r.status_code == 422


@pytest.mark.parametrize("via", ["duplicate", "workspace_template"])
def test_copied_bot_loads(client, via):
    """The copy used to gain an ``agent_config`` block that BotConfig
    forbids: the bot couldn't load and the next boot failed."""
    from models.bots import Bots
    from services.bot_config import get_config_for_bot

    legacy = {"agent_config": {"bot_slug": "old", "bot_name": "Old"}, "enrichment_enabled": True}
    if via == "duplicate":
        slug, r = _create_bot(client, source_bot_id=_bot(legacy).id)
    else:
        slug, r = _create_bot(client, template_id=f"workspace:{_template(legacy)}")
    assert r.status_code == 201, r.text

    bot = Bots.get_by_slug(slug)
    assert "agent_config" not in (bot.config_json or {})
    # The same parse boot validation runs for every bot.
    assert get_config_for_bot(bot.id).enrichment_enabled is True


def test_duplicate_keeps_a_bad_value_already_stored_on_the_source(client):
    _, r = _create_bot(client, source_bot_id=_bot(BAD_COLOUR).id)
    assert r.status_code == 201, r.text


def test_every_built_in_template_passes_the_gate():
    from services.bot_templates import get_template_config, list_templates
    from services.config_gate import check_new_config

    for t in list_templates():
        check_new_config(get_template_config(t.id, "slug-x", "Name X"))
