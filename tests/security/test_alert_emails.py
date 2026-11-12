"""D6: per-bot option to send form alerts as a link only."""

from __future__ import annotations

import asyncio
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


def test_full_email_unchanged_by_default(scene, monkeypatch):
    make, sent = scene
    monkeypatch.setattr(widget_email, "_summarize_conversation", lambda *a, **k: "summary")
    bot, session, files = make()

    _send(bot, session, files)

    (msg,) = sent
    html = _html(msg)
    assert "998877" in html and "ann@example.com" in html
    assert _attachments(msg) == ["passport.png"]


@pytest.mark.parametrize("content, expect_files", [(None, True), ("link_only", False)])
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

    assert BotConfig().alert_email_content == "full"
    assert BotConfig(alert_email_content="link_only").alert_email_content == "link_only"
    with pytest.raises(ValidationError):
        BotConfig(alert_email_content="transcript")
