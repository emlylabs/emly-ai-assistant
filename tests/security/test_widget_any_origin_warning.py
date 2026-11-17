"""G11b: in production, /api/readyz names bots whose widget any website
can embed (``*`` in Allowed widget origins, e.g. after migration 010)."""
from __future__ import annotations

import uuid


def _bot(origins):
    from models.bots import Bots

    return Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"o-{uuid.uuid4().hex[:6]}", name="O",
                       config_json={"limits": {"widget_allowed_origins": origins}})


def test_readyz_lists_bots_open_to_any_site_in_prod(isolated_db, monkeypatch):
    import main
    from models.bots import Bot

    open_bot, locked = _bot(["*"]), _bot(["https://www.example.com"])
    try:
        monkeypatch.setenv("MODE", "prod")
        (w,) = [w for w in main._collect_runtime_warnings() if w["code"] == "widget_any_origin"]
        assert open_bot.slug in w["message"] and locked.slug not in w["message"]

        monkeypatch.setenv("MODE", "dev")
        assert not [w for w in main._collect_runtime_warnings() if w["code"] == "widget_any_origin"]
    finally:
        Bot.delete().where(Bot.id.in_([open_bot.id, locked.id])).execute()
