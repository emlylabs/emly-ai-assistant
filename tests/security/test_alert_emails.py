"""D6: per-bot option to send form alerts as a link only."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from services import widget_email


def _uid(p):
    return f"{p}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def scene(isolated_db, tmp_path, monkeypatch):
    from models.bots import Bots
    from models.emly_messages import EMLYMessages
    from models.emly_users import EMLYUsers

    sent = []

    class FakeSender:
        def __init__(self, **kw):
            pass

        def send_email(self, msg):
            sent.append(msg)
            return True

    monkeypatch.setattr(widget_email, "EmailSender", FakeSender)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://admin.example.com")

    def make(content=None):
        config = {"alert_email_content": content} if content else {}
        bot = Bots.insert(id=_uid("bot"), slug=_uid("al"), name="Acme Support", config_json=config)
        EMLYUsers.insert_new_user(bot_id=bot.id, id="v1", first_name="Ann", last_name=None,
                                  email="ann@example.com", phone="+1-555-0100", ip="", browser="", meta=None)
        session = _uid("sess")
        EMLYMessages.insert_new_message(bot_id=bot.id, user_id="v1", session_id=session, role="user",
                                        message="my account number is 998877", not_useful=False,
                                        expanded_query=None, page="p")
        attachment = tmp_path / "passport.png"
        attachment.write_bytes(b"PNGDATA")
        files = [{"filename": "passport.png", "content_type": "image/png", "size": 7, "path": str(attachment)}]
        return bot, session, files

    return make, sent


def _send(bot, session, files):
    widget_email.send_scheduled_email("alerts@example.com", "v1", {"session_id": session}, "Callback request",
                                      files, "p", "", bot.id)


def _html(msg) -> str:
    return "".join(p.get_payload(decode=True).decode() for p in msg.walk() if p.get_content_type() == "text/html")


def _attachments(msg) -> list:
    return [p.get_filename() for p in msg.walk() if p.get_filename()]


def test_link_only_email_has_no_visitor_data(scene, monkeypatch):
    make, sent = scene
    monkeypatch.setattr(widget_email, "_summarize_conversation",
                        lambda *a, **k: pytest.fail("link-only alerts must not call the LLM"))
    bot, session, files = make("link_only")

    _send(bot, session, files)

    (msg,) = sent
    html = _html(msg)
    assert f"https://admin.example.com/bots/{bot.slug}/conversations?session_id={session}" in html
    assert "Acme Support" in html and "Callback request" in html
    for leaked in ("998877", "ann@example.com", "555-0100", "Ann"):
        assert leaked not in html, leaked
    assert _attachments(msg) == []


def test_full_email_has_the_details(scene, monkeypatch):
    make, sent = scene
    monkeypatch.setattr(widget_email, "_summarize_conversation", lambda *a, **k: "summary")
    bot, session, files = make("full")

    _send(bot, session, files)

    (msg,) = sent
    html = _html(msg)
    assert "998877" in html and "ann@example.com" in html
    assert _attachments(msg) == ["passport.png"]


@pytest.mark.parametrize("content, expect_files", [("full", True), ("link_only", False), (None, False)])
def test_queued_job_holds_only_what_it_needs(scene, monkeypatch, content, expect_files):
    make, _ = scene
    bot, session, files = make(content)
    jobs = []
    monkeypatch.setattr(widget_email.scheduler, "add_job", lambda fn, **kw: jobs.append(kw["args"]))
    form = SimpleNamespace(session_id=session, dict=lambda: {
        "session_id": session, "action_payload": {"phone": "+1-555-0100", "notes": "998877"}})

    asyncio.run(widget_email.send_email_with_attachments(
        "alerts@example.com", SimpleNamespace(id="v1"), form, "Callback request", files, "p", "", bot_id=bot.id))

    (args,) = jobs
    assert args[2] == {"session_id": session}  # no form payload in the persistent job store
    assert args[4] == (files if expect_files else [])


def test_option_is_validated():
    from pydantic import ValidationError

    from services.bot_config import BotConfig

    assert BotConfig().alert_email_content == "link_only"  # G1c: new bots
    assert BotConfig(alert_email_content="full").alert_email_content == "full"
    assert BotConfig(alert_email_content="link_only").alert_email_content == "link_only"
    with pytest.raises(ValidationError):
        BotConfig(alert_email_content="transcript")


def test_new_bot_without_a_choice_gets_link_only_alerts(scene, monkeypatch):
    """G1c: a bot that never picked a mode sends only the link."""
    make, sent = scene
    monkeypatch.setattr(widget_email, "_summarize_conversation",
                        lambda *a, **k: pytest.fail("link-only alerts must not call the LLM"))
    bot, session, files = make()

    _send(bot, session, files)

    (msg,) = sent
    assert "998877" not in _html(msg)
    assert _attachments(msg) == []


# --------------------------------------------------------------- migration 013
def _load_migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "migrations" / "013_alert_email_content_explicit.py"
    spec = importlib.util.spec_from_file_location("mig013", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "raw, expected",
    [
        (json.dumps({"enrichment_enabled": True}), {"enrichment_enabled": True, "alert_email_content": "full"}),
        (json.dumps({}), {"alert_email_content": "full"}),
        ("", None),
        (json.dumps({"alert_email_content": "link_only"}), None),
        (json.dumps({"alert_email_content": "full"}), None),
        (json.dumps(["not", "a", "dict"]), None),
        ("not json", None),
        (None, None),
    ],
)
def test_migration_transform(raw, expected):
    out = _load_migration()._with_explicit_full(raw)
    assert (json.loads(out) if out else None) == expected


def test_migration_keeps_existing_bots_on_full(isolated_db):
    from db.db import DB
    from models.bots import Bot, Bots
    from services.bot_config import get_config_for_bot

    legacy = Bots.insert(id=_uid("bot"), slug=_uid("l"), name="L", config_json={"enrichment_enabled": False})
    chosen = Bots.insert(id=_uid("bot"), slug=_uid("c"), name="C", config_json={"alert_email_content": "link_only"})
    Bot.update(config_draft_json=json.dumps({"enrichment_enabled": True})).where(Bot.id == legacy.id).execute()

    # Before the migration, the legacy bot would read as link_only under the new default.
    assert get_config_for_bot(legacy.id).alert_email_content == "link_only"

    _load_migration()._backfill(DB, "bots", ("config_json", "config_draft_json"))

    assert get_config_for_bot(legacy.id).alert_email_content == "full"
    assert get_config_for_bot(chosen.id).alert_email_content == "link_only"
    assert json.loads(Bot.get_by_id(legacy.id).config_draft_json)["alert_email_content"] == "full"
