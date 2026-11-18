"""S11: invite emails can't carry an admin's markup, and invites are limited.

A bot owner chooses the bot's name and the addresses to invite. The name
went unescaped into the email HTML and raw into the Subject, so an owner
could have the customer's mail relay deliver their HTML to any address,
as often as they liked.
"""
from __future__ import annotations

import pytest

from services import invite_email


@pytest.fixture
def sent(monkeypatch):
    out = []

    class _Sender:
        def __init__(self, **kw):
            pass

        def send_email(self, msg):
            out.append(msg)

    monkeypatch.setattr(invite_email, "EmailSender", _Sender)
    monkeypatch.setattr(invite_email, "_read_template",
                        lambda: "<p>{inviter_name} invited you to {space_name}: <a href='{accept_url}'>Accept</a></p>")
    return out


def test_names_are_escaped_and_subject_is_one_line(sent):
    invite_email._send_invite_email({
        "email": "new@corp.example",
        "inviter_name": "<img src=https://evil.example/p>",
        "space_name": "HR</p><a href='https://evil.example'>Reset password</a>\r\nBcc: x@evil.example",
        "accept_url": "https://emly.corp.example/accept-invite?token=t&x=1",
        "role": "viewer", "inviter_email": "", "expires_human": "",
    })
    (msg,) = sent
    body = msg.get_payload()[0].get_payload(decode=True).decode()
    assert "<img" not in body and "<a href='https://evil.example'" not in body
    assert "&lt;img src=https://evil.example/p&gt;" in body
    assert "href='https://emly.corp.example/accept-invite?token=t&amp;x=1'" in body
    subject = str(msg["Subject"])
    assert "\n" not in subject and "\r" not in subject
    assert msg["Bcc"] is None


def test_invites_are_rate_limited_per_admin(monkeypatch):
    from fastapi import HTTPException

    from services.auth import ratelimit

    monkeypatch.setitem(ratelimit._LIMITS, "admin_invite", "2/minute")
    ratelimit.limiter.reset()
    ratelimit.hit("admin_invite", "adm-a")
    ratelimit.hit("admin_invite", "adm-a")
    with pytest.raises(HTTPException) as exc:
        ratelimit.hit("admin_invite", "adm-a")
    assert exc.value.status_code == 429
    ratelimit.hit("admin_invite", "adm-b")  # another admin has their own budget


def test_invite_route_counts_against_the_limit():
    import inspect

    from routes import admin_admins

    src = inspect.getsource(admin_admins.create_space_invite)
    assert 'hit("admin_invite", admin.id)' in src
