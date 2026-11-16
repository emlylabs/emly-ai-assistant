"""G7d: a "likely resolved" estimate, reported beside manual resolution."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def bot(isolated_db):
    from models.bots import Bots

    return Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"ar-{uuid.uuid4().hex[:6]}", name="AR")


def _session(bot_id, *, answered=True, rating=None, idle=False, ended=False, taken_over=False,
             resolved=None, handed_off=False):
    from models.emly_messages import EMLYMessage, EMLYMessages
    from models.emly_sessions import EMLYSession

    sid = f"s-{uuid.uuid4().hex[:8]}"
    EMLYMessages.insert_new_message(bot_id=bot_id, user_id="u", session_id=sid, role="user",
                                    message="q", not_useful=False, expanded_query=None, page="p")
    if answered:
        m = EMLYMessages.insert_new_message(bot_id=bot_id, user_id="u", session_id=sid, role="assistant",
                                            message="a", not_useful=False, expanded_query=None, page="p")
        mid = m["id"] if isinstance(m, dict) else m.id
        EMLYMessage.update(rating=rating, is_deflected=False if handed_off else None).where(
            EMLYMessage.id == mid).execute()
    last = _now() - (timedelta(hours=2) if idle else timedelta(minutes=1))
    EMLYSession.update(
        last_activity_at=last,
        ended_at=_now() if ended else None,
        taken_over_at=_now() if taken_over else None,
        is_resolved=resolved,
    ).where(EMLYSession.id == sid).execute()
    return sid


def _report(bot_id):
    from models.emly_messages import EMLYMessages

    return EMLYMessages.get_report(bot_id, _now() - timedelta(days=1), _now() + timedelta(minutes=5))


def test_likely_resolved_counts_only_quiet_or_thumbs_up_sessions(bot):
    likely = [
        _session(bot.id, rating=1),                 # thumbs-up, still active
        _session(bot.id, idle=True),                # answered, visitor went quiet
        _session(bot.id, ended=True),               # answered, session ended
    ]
    _session(bot.id)                                # answered, still going
    _session(bot.id, idle=True, rating=-1)          # thumbs-down
    _session(bot.id, idle=True, taken_over=True)    # staff took over
    _session(bot.id, idle=True, handed_off=True)    # bot handed off to a human
    _session(bot.id, idle=True, answered=False)     # never answered
    _session(bot.id, idle=True, resolved=True)      # staff marked resolved
    _session(bot.id, idle=True, resolved=False)     # staff escalated

    r = _report(bot.id)
    assert r["likely_resolved_count"] == len(likely)
    assert r["likely_resolved_rate"] == round(3 / 10, 4)
    assert r["resolution_rate"] == round(1 / 10, 4)  # manual stays separate


def test_no_sessions_means_no_rate(bot):
    r = _report(bot.id)
    assert r["likely_resolved_rate"] is None and r["likely_resolved_count"] == 0


def test_report_endpoint_and_export_carry_it(bot):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from models.admin_users import AdminUsers
    from routes import admin_bot_dashboard
    from services.auth.dependencies import get_admin

    _session(bot.id, idle=True)
    admin = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="ar@example.com", is_active=True,
                              is_superadmin=True)
    app = FastAPI()
    app.include_router(admin_bot_dashboard.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: admin
    client = TestClient(app)

    report = client.get(f"/api/admin/bots/{bot.slug}/report").json()
    assert report["likely_resolved_rate"] == 1.0 and report["likely_resolved_count"] == 1
    csv = client.get(f"/api/admin/bots/{bot.slug}/analytics/export").text
    assert "likely_resolved_rate_automatic" in csv
