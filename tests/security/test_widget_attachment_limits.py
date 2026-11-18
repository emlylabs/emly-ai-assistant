"""S17: visitors can attach files only to forms that ask for them, within a
per-bot quota, and one submission makes one summary call.

File handling ran for any action, whether or not a form with a file field
existed; the storage quota and file-count cap applied only to admin
uploads; and each alert recipient's job made its own summary call, which
the token cap doesn't count.
"""
from __future__ import annotations

import json

from tests.security.test_rate_limits import _otp_payload, small_limits, widget_env  # noqa: F401


def _post(env, bot, form_title, with_file=True):
    payload = {"session_id": "s1", "action_name": "submit", "action_value": "x"}
    if form_title:
        payload["form_title"] = form_title
    files = {"files": ("a.txt", b"hello", "text/plain")} if with_file else None
    return env["client"].post(f"/widget/{bot.id}/action", headers=env["auth"](bot),
                              data={"payload": json.dumps(payload)}, files=files)


def test_files_only_on_forms_with_a_file_field(widget_env, monkeypatch):  # noqa: F811
    from routes import widget as widget_routes
    from services.auth import ratelimit

    monkeypatch.setitem(ratelimit._LIMITS, "widget_action", "100/minute")
    monkeypatch.setitem(ratelimit._LIMITS, "widget_upload", "100/minute")

    async def _store(files, user_id, bot_id):
        return [{"filename": f.filename} for f in files or []]

    monkeypatch.setattr(widget_routes, "handle_file_uploads", _store)
    bot = widget_env["make_bot"]()
    assert _post(widget_env, bot, None).status_code == 400          # plain action
    assert _post(widget_env, bot, "contact").status_code == 400     # form without a file field
    assert _post(widget_env, bot, "nope").status_code == 400        # unknown form
    assert _post(widget_env, bot, "uploads").status_code == 200
    assert _post(widget_env, bot, None, with_file=False).status_code == 200


def test_quota_refuses_new_attachments(widget_env, monkeypatch):  # noqa: F811
    from routes import widget as widget_routes

    monkeypatch.setattr(widget_routes, "attachment_quota_allows", lambda bot_id, incoming: False)
    bot = widget_env["make_bot"]()
    assert _post(widget_env, bot, "uploads").status_code == 507


def test_quota_counts_visitor_attachments_only(isolated_db, monkeypatch):
    import uuid

    from models.bots import Bots
    from models.emly_files import Emly_Files
    from models.emly_users import EMLYUsers
    from services import widget_uploads

    monkeypatch.setenv("WIDGET_ATTACHMENT_QUOTA_MB", "1")
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"q-{uuid.uuid4().hex[:6]}", name="Q")
    EMLYUsers.insert_new_user(bot_id=bot.id, id="v1", first_name=None, last_name=None, email="",
                              phone="", ip="", browser="", meta=None)
    Emly_Files.insert_new_file(bot_id=bot.id, file_name="doc.pdf", file_type="application/pdf",
                               file_size=5 * 1024 * 1024, size_bytes=5 * 1024 * 1024)  # admin doc
    assert widget_uploads.attachment_quota_allows(bot.id, 900 * 1024)
    Emly_Files.insert_new_file(bot_id=bot.id, user_id="v1", file_name="a.txt", file_type="text/plain",
                               file_size=600 * 1024, size_bytes=600 * 1024)
    assert not widget_uploads.attachment_quota_allows(bot.id, 500 * 1024)


def test_one_summary_for_all_recipients(monkeypatch):
    from services import widget_email

    summaries, sent = [], []
    monkeypatch.setattr(widget_email, "_alert_email_content", lambda bot_id: "full")
    monkeypatch.setattr(widget_email.EMLYUsers, "get_user_by_id", lambda b, u: type("U", (), {"meta": {}})())
    monkeypatch.setattr(widget_email, "get_html_content_with_summarized_messages",
                        lambda *a, **k: (summaries.append(1) or "<p>s</p>", None))
    monkeypatch.setattr(widget_email, "_send_one_alert", lambda r, *a: sent.append(r))
    monkeypatch.setattr(widget_email.EMLYMessages, "insert_new_message", lambda **k: None)
    widget_email.send_scheduled_email(["a@corp.example", "b@corp.example"], "v1", {"session_id": "s"},
                                      "Leave", [], None, "", "bot-1")
    assert summaries == [1]
    assert sent == ["a@corp.example", "b@corp.example"]
