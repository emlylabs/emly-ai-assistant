"""Widget config passes ``enable_voice`` and ``voice_language`` through, and drops wrong types."""
from __future__ import annotations

import uuid

from models.bots import Bots
from routes.widget import _build_widget_config


def _bot(config):
    suffix = uuid.uuid4().hex[:8]
    return Bots.insert(
        id=f"bot-{suffix}",
        slug=f"voice-{suffix}",
        name="Voice",
        config_json=config,
    )


def test_build_widget_config_returns_enable_voice(isolated_db):
    on = _build_widget_config(_bot({"schema_version": 1, "enable_voice": True}))
    assert on.enable_voice is True

    missing = _build_widget_config(_bot({"schema_version": 1}))
    assert missing.enable_voice is None


def test_build_widget_config_drops_non_bool_enable_voice(isolated_db, monkeypatch):
    bot = _bot({"schema_version": 1, "enable_voice": True})

    class FakeConfig:
        def model_dump(self, mode="json"):
            return {"enable_voice": "yes"}

    monkeypatch.setattr("routes.widget.get_config_for_bot", lambda _bot_id: FakeConfig())
    assert _build_widget_config(bot).enable_voice is None


def test_build_widget_config_returns_voice_language(isolated_db):
    cfg = _build_widget_config(
        _bot({"schema_version": 1, "enable_voice": True, "voice_language": "es-MX"})
    )
    assert cfg.voice_language == "es-MX"

    missing = _build_widget_config(_bot({"schema_version": 1}))
    assert missing.voice_language is None


def test_build_widget_config_drops_non_str_voice_language(isolated_db, monkeypatch):
    bot = _bot({"schema_version": 1, "enable_voice": True})

    class FakeConfig:
        def model_dump(self, mode="json"):
            return {"voice_language": 42}

    monkeypatch.setattr("routes.widget.get_config_for_bot", lambda _bot_id: FakeConfig())
    assert _build_widget_config(bot).voice_language is None
