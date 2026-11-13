"""B3: per-bot widget origins — safe default for new bots, enforcement,
the app's own origin always allowed, existing bots migrated to explicit *."""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_migration():
    path = REPO_ROOT / "migrations" / "010_widget_origins_explicit.py"
    spec = importlib.util.spec_from_file_location("mig010", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- defaults


def test_new_bot_default_is_empty():
    from services.bot_config import BotConfig

    assert BotConfig().limits.widget_allowed_origins == []


# ------------------------------------------------------------- enforcement


@pytest.fixture
def cors_app(isolated_db, monkeypatch):
    from models.bots import Bots
    from services.auth import widget_cors

    monkeypatch.setenv("APP_BASE_URL", "https://emly.example.com")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    widget_cors._cache.clear()

    def make_bot(origins):
        cfg = {"limits": {"widget_allowed_origins": origins}} if origins is not None else {}
        return Bots.insert(
            id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"c-{uuid.uuid4().hex[:6]}", name="C", config_json=cfg
        )

    app = FastAPI()
    app.add_middleware(widget_cors.WidgetCORSMiddleware)

    @app.get("/widget/{bot_ref}/config")
    def _cfg(bot_ref: str):
        return {"ok": True}

    yield TestClient(app), make_bot
    widget_cors._cache.clear()


def test_origin_not_on_list_is_rejected(cors_app):
    client, make_bot = cors_app
    bot = make_bot(["https://www.customer.com"])
    r = client.get(f"/widget/{bot.id}/config", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "widget_origin_rejected"


def test_origin_on_list_is_allowed(cors_app):
    client, make_bot = cors_app
    bot = make_bot(["https://www.customer.com"])
    r = client.get(f"/widget/{bot.id}/config", headers={"Origin": "https://www.customer.com"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://www.customer.com"


def test_new_bot_rejects_other_sites_but_allows_own_origin(cors_app):
    client, make_bot = cors_app
    bot = make_bot(None)  # no explicit origins → new default []
    assert client.get(f"/widget/{bot.id}/config", headers={"Origin": "https://www.customer.com"}).status_code == 403
    own = client.get(f"/widget/{bot.id}/config", headers={"Origin": "https://emly.example.com"})
    assert own.status_code == 200
    assert own.headers["access-control-allow-origin"] == "https://emly.example.com"


def test_preflight_only_grants_cors_to_allowed_origin(cors_app):
    client, make_bot = cors_app
    bot = make_bot(["https://www.customer.com"])
    headers = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    ok = client.options(f"/widget/{bot.id}/config", headers={**headers, "Origin": "https://www.customer.com"})
    assert ok.headers.get("access-control-allow-origin") == "https://www.customer.com"
    bad = client.options(f"/widget/{bot.id}/config", headers={**headers, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_wildcard_still_allows_any(cors_app):
    client, make_bot = cors_app
    bot = make_bot(["*"])
    r = client.get(f"/widget/{bot.id}/config", headers={"Origin": "https://anything.example"})
    assert r.status_code == 200


# --------------------------------------------------------------- migration


@pytest.mark.parametrize(
    "raw, expected",
    [
        (json.dumps({"limits": {"max_file_size_mb": 5}}), {"limits": {"max_file_size_mb": 5, "widget_allowed_origins": ["*"]}}),
        (json.dumps({"agent_config": {}}), {"agent_config": {}, "limits": {"widget_allowed_origins": ["*"]}}),
        (json.dumps({"limits": {"widget_allowed_origins": ["https://a.com"]}}), None),
        (json.dumps({"limits": {"widget_allowed_origins": []}}), None),
        ("not json", None),
        (None, None),
    ],
)
def test_migration_transform(raw, expected):
    out = _load_migration()._with_explicit_wildcard(raw)
    assert (json.loads(out) if out else None) == expected


def test_migration_backfills_existing_bots(isolated_db):
    from db.db import DB
    from models.bots import Bot, Bots
    from services.bot_config import get_config_for_bot

    legacy = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"l-{uuid.uuid4().hex[:6]}", name="L",
                         config_json={"limits": {}})
    explicit = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"e-{uuid.uuid4().hex[:6]}", name="E",
                           config_json={"limits": {"widget_allowed_origins": ["https://a.com"]}})
    Bot.update(config_draft_json=json.dumps({"agent_config": {}})).where(Bot.id == legacy.id).execute()

    # Before the migration, the legacy bot would read as [] under the new default.
    assert get_config_for_bot(legacy.id).limits.widget_allowed_origins == []

    mig = _load_migration()
    mig._backfill(DB, "bots", ("config_json", "config_draft_json"))

    assert get_config_for_bot(legacy.id).limits.widget_allowed_origins == ["*"]
    assert get_config_for_bot(explicit.id).limits.widget_allowed_origins == ["https://a.com"]
    draft = json.loads(Bot.get_by_id(legacy.id).config_draft_json)
    assert draft["limits"]["widget_allowed_origins"] == ["*"]


def test_rt2_is_a_warning_not_a_save_blocking_error():
    from services.config_validation import ValidationContext, validate_config

    issues = validate_config({"limits": {"widget_allowed_origins": []}}, ValidationContext())
    rt2 = [i for i in issues if i.key == "RT2_widget_origins_empty"]
    assert rt2 and rt2[0].severity == "warning"
