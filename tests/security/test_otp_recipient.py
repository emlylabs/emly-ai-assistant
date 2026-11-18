"""S3: a sign-in code goes to exactly one plain address, on a bot that
verifies email, and the email can't carry the requester's markup.

Before: the OTP ``user_id`` went raw into the ``To`` header and unescaped
into the HTML, so ``"<a href=...>x</a>" <victim@corp>, other@corp`` sent a
genuine email from the customer's relay to several victims, and the
per-recipient limit was keyed on the raw string.
"""
from __future__ import annotations

import pytest

from services import otp
from tests.security.test_rate_limits import _otp_payload, small_limits, widget_env  # noqa: F401


@pytest.mark.parametrize("value", [
    "victim@corp.example",
    "  Victim@Corp.Example ",
    "first.last+tag@sub.corp.example",
])
def test_single_addresses_accepted_and_lowercased(value):
    assert otp.normalize_otp_email(value) == value.strip().lower()


@pytest.mark.parametrize("value", [
    "",
    None,
    "no-at-sign",
    "a@b",                                      # no dot in the domain
    "Name <victim@corp.example>",               # display name
    "victim@corp.example, other@corp.example",  # list
    "victim@corp.example;other@corp.example",
    "victim@corp.example\r\nBcc: x@corp.example",
    "<b>hi</b>@corp.example",
    "a b@corp.example",
    "x" * 250 + "@corp.example",
])
def test_anything_else_refused(value):
    assert otp.normalize_otp_email(value) is None


def test_email_html_is_escaped_and_sent_to_one_address(monkeypatch):
    sent = []

    class _Sender:
        def __init__(self, **kw):
            pass

        def send_email(self, msg):
            sent.append(msg)

    monkeypatch.setattr(otp, "EmailSender", _Sender)
    monkeypatch.setattr(otp, "EMAIL_OTP_TEMPLATE", "Dear {first_name}, code {otp_code} for {email}")
    otp.send_otp_in_email({"user_id": "Bob.O'Neil@Corp.Example", "otp": "123456"})
    (msg,) = sent
    assert msg["To"] == "bob.o'neil@corp.example"
    body = msg.get_payload()[0].get_payload(decode=True).decode()
    assert "bob.o&#x27;neil" in body and "bob.o'neil" not in body

    with pytest.raises(ValueError):
        otp.send_otp_in_email({"user_id": "a@corp.example, b@corp.example", "otp": "1"})


def _send(env, bot, user_id):
    return env["client"].post(f"/widget/{bot.id}/action", headers=env["auth"](bot),
                              data={"payload": _otp_payload(user_id)})


def test_route_refuses_lists_and_display_names(widget_env, monkeypatch):  # noqa: F811
    from routes import widget as widget_routes

    created = []
    monkeypatch.setattr(widget_routes, "create_otp", lambda payload, bot_id: created.append(payload))
    monkeypatch.setattr(widget_routes, "email_enabled", lambda: True)
    bot = widget_env["make_bot"]()
    for bad in ("Name <v@corp.example>", "v@corp.example, w@corp.example"):
        r = _send(widget_env, bot, bad)
        assert r.status_code == 400, r.text
    assert created == []
    assert _send(widget_env, bot, " V@Corp.Example ").status_code == 200
    assert created[0]["user_id"] == "v@corp.example"


def test_route_refuses_a_bot_without_email_verification(widget_env, monkeypatch):  # noqa: F811
    import uuid

    from models.bots import Bots
    from routes import widget as widget_routes

    created = []
    monkeypatch.setattr(widget_routes, "create_otp", lambda payload, bot_id: created.append(payload))
    monkeypatch.setattr(widget_routes, "email_enabled", lambda: True)
    plain = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"p-{uuid.uuid4().hex[:6]}", name="P")
    r = _send(widget_env, plain, "v@corp.example")
    assert r.status_code == 404
    assert created == []


def test_verify_uses_the_normalised_address(widget_env, monkeypatch):  # noqa: F811
    from routes import widget as widget_routes

    seen = []
    monkeypatch.setattr(widget_routes, "authorize_otp",
                        lambda payload, bot_id: seen.append(payload["user_id"]) or {"code": 200, "message": "ok"})
    bot = widget_env["make_bot"]()
    r = widget_env["client"].post(f"/widget/{bot.id}/action", headers=widget_env["auth"](bot),
                                  data={"payload": _otp_payload(" V@Corp.Example", "auth_verification", otp="1")})
    assert r.status_code == 200
    assert seen == ["v@corp.example"]
