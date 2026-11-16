"""N4: one bot whose stored config doesn't load no longer stops the app.

``boot_validate_all_configs`` re-raised, so a single bad row (a bad
template, a hand edit, a schema change without an upgrade step) took
every bot down at the next start. Now that bot is logged and listed on
readyz, answers "not configured", and its config can still be opened in
the admin editor so it can be fixed.
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import pytest

SECRET = "internal escalation prompt 7731"


@pytest.fixture
def bad_bot(isolated_db):
    from models.bots import Bot, Bots

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"bad-{uuid.uuid4().hex[:6]}", name="Bad",
                      config_json={"legacy_prompt": SECRET, "show_citations": "maybe"})
    yield bot
    Bot.delete().where(Bot.id == bot.id).execute()  # later tests boot main


def test_boot_reports_instead_of_raising(bad_bot, caplog):
    from services.bot_config import boot_validate_all_configs

    with caplog.at_level(logging.ERROR, logger="services.bot_config"):
        broken = boot_validate_all_configs()

    assert set(broken[bad_bot.id].split("; ")) == {"legacy_prompt: extra_forbidden",
                                                   "show_citations: bool_parsing"}
    assert bad_bot.id in caplog.text and "won't answer" in caplog.text
    assert SECRET not in caplog.text and "maybe" not in caplog.text


def test_readyz_lists_the_bot_until_fixed(bad_bot):
    import main
    from models.bots import Bots
    from services.bot_config import boot_validate_all_configs

    boot_validate_all_configs()
    (warning,) = [w for w in main._collect_runtime_warnings() if w["code"] == "bot_config_invalid"]
    assert bad_bot.id in warning["message"] and SECRET not in warning["message"]

    Bots.update_config(bad_bot.id, {})
    assert not [w for w in main._collect_runtime_warnings() if w["code"] == "bot_config_invalid"]


@pytest.mark.parametrize("stream", [False, True])
def test_bot_answers_not_configured(bad_bot, stream):
    from agents.conversation_agent import create_config
    from services.agent_service import AgentService
    from services.llm_settings import NOT_CONFIGURED_REPLY

    svc = AgentService()
    svc.session_manager = SimpleNamespace(get_handler=lambda bot_id, *a: create_config(bot_id))
    out = svc.process_message(bot_id=bad_bot.id, user_id="u", session_id="s", page_id="p",
                              message="hi", stream=stream)
    if stream:
        out = next(iter(out))["data"]
    else:
        out = out[0]
    assert out == NOT_CONFIGURED_REPLY


def test_admin_can_open_the_config_to_fix_it(bad_bot):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from models.admin_users import AdminUsers
    from routes import admin_bot_dashboard
    from services.auth.dependencies import get_admin

    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="fix@corp.example", is_active=True,
                              is_superadmin=True)
    app = FastAPI()
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: admin

    r = TestClient(app).get(f"/api/admin/bots/{bad_bot.slug}/config")

    assert r.status_code == 200
    body = r.json()
    assert body["config"]["legacy_prompt"] == SECRET  # the stored JSON, so it can be edited
    assert "legacy_prompt: extra_forbidden" in body["load_error"]
    assert SECRET not in body["load_error"]
