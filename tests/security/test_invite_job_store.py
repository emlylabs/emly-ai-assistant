"""N7: the invite token never reaches the persistent job store.

Invite emails are sent by an APScheduler job, and the default job store
is a file on disk (``actions.db``). The job used to carry the rendered
payload: the accept URL with the invite token, the invitee's email and
the inviter's email. Anyone who could read that file could accept the
invite. The job now carries only ids, the space name and the role, and
looks up the rest when it runs.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def invite(isolated_db, monkeypatch):
    from models.admin_users import AdminUsers
    from models.bots import Bots
    from models.pending_admins import PendingAdmins
    from services import invite_email

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://emly.example.com/")
    queued, sent = [], []
    monkeypatch.setattr(invite_email.scheduler, "add_job", lambda fn, args: queued.append((fn, args)))
    monkeypatch.setattr(invite_email, "_send_invite_email",
                        lambda payload, ref=None: sent.append(payload))
    inviter = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="owner@corp.example",
                                name="Olive Owner", is_active=True)
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"i-{uuid.uuid4().hex[:6]}", name="HR")
    pending = PendingAdmins.create_space_invite(email="new.hire@corp.example", invited_by=inviter.id,
                                                target_bot_id=bot.id, role="viewer")
    return pending, inviter, queued, sent


def _queue(pending, inviter):
    from routes.admin_admins import _try_send_invite_email

    _try_send_invite_email(pending=pending, inviter=inviter, space_name="HR", role="viewer")


def _run(queued):
    (fn, args), = queued
    fn(*args)


def test_job_arguments_hold_no_token_or_addresses(invite):
    pending, inviter, queued, _ = invite
    _queue(pending, inviter)
    (_, args), = queued
    stored = json.dumps(args)
    for secret in (pending.token, "new.hire@corp.example", "owner@corp.example", "accept-invite"):
        assert secret not in stored
    assert args == [pending.id, inviter.id, "HR", "viewer"]


def test_job_builds_the_email_when_it_runs(invite):
    pending, inviter, queued, sent = invite
    _queue(pending, inviter)
    _run(queued)
    (payload,) = sent
    assert payload["accept_url"] == f"https://emly.example.com/accept-invite?token={pending.token}"
    assert payload["email"] == "new.hire@corp.example"
    assert (payload["inviter_name"], payload["inviter_email"]) == ("Olive Owner", "owner@corp.example")
    assert (payload["space_name"], payload["role"]) == ("HR", "viewer")


def test_revoked_invite_is_not_sent(invite):
    from models.pending_admins import PendingAdmins

    pending, inviter, queued, sent = invite
    _queue(pending, inviter)
    PendingAdmins.mark_revoked(pending.id, inviter.id)
    _run(queued)
    assert sent == []


def test_expired_invite_is_not_sent(invite):
    from models.pending_admins import PendingAdmin

    pending, inviter, queued, sent = invite
    _queue(pending, inviter)
    PendingAdmin.update(expires_at=datetime.utcnow() - timedelta(minutes=1)).where(
        PendingAdmin.id == pending.id).execute()
    _run(queued)
    assert sent == []


def test_no_public_url_queues_nothing(invite, monkeypatch):
    pending, inviter, queued, _ = invite
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    _queue(pending, inviter)
    assert queued == []


def test_jobs_queued_before_the_change_still_send(isolated_db, monkeypatch):
    """Old jobs call ``_send_invite_email(payload)``; they drain on start."""
    from services import invite_email

    sent = []
    monkeypatch.setattr(invite_email, "EmailSender",
                        lambda **kw: type("S", (), {"send_email": lambda self, msg: sent.append(msg["To"])})())
    invite_email._send_invite_email({"email": "a@corp.example", "space_name": "HR",
                                     "accept_url": "https://x/accept-invite?token=t"})
    assert sent == ["a@corp.example"]
