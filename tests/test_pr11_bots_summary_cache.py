"""PR #11: the cached ``/bots/summary`` list must not serve a soft-deleted
bot.

The 60s cache entry now records the set of live bot ids it covers and is
only reused while that set is unchanged. So a delete shows up immediately,
both through this worker's DELETE route (which also clears the cache) and
when another worker did the delete (simulated by ``Bots.soft_delete``
directly, leaving this process's cache untouched).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def summary_env(isolated_db):
    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers
    from models.bots import Bot, Bots
    from routes import admin_bots
    from services.auth.dependencies import get_admin

    admin_bots._SUMMARY_CACHE.clear()
    owner = AdminUsers.insert(id=f"adm-{uuid.uuid4().hex[:6]}", email="own@x.example", is_active=True)
    bots = [
        Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"s{i}-{uuid.uuid4().hex[:6]}", name=f"S{i}")
        for i in range(2)
    ]
    for b in bots:
        AdminBotMemberships.grant(id=f"mbr-{uuid.uuid4().hex[:8]}", admin_id=owner.id,
                                  bot_id=b.id, role="owner")

    app = FastAPI()
    app.include_router(admin_bots.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: owner
    yield TestClient(app), bots

    admin_bots._SUMMARY_CACHE.clear()
    Bot.delete().where(Bot.id.in_([b.id for b in bots])).execute()


def _slugs(client) -> list[str]:
    r = client.get("/api/admin/bots/summary")
    assert r.status_code == 200, r.text
    return sorted(row["slug"] for row in r.json())


def test_summary_is_cached_while_bot_set_unchanged(summary_env):
    """Baseline: the cache is in use, so the delete tests below are testing
    invalidation rather than a cache that never hits."""
    from models.bots import Bot

    client, bots = summary_env
    assert _slugs(client) == sorted(b.slug for b in bots)
    # A rename alone (no delete/create) is served from cache within the TTL.
    Bot.update(name="renamed").where(Bot.id == bots[0].id).execute()
    names = {row["slug"]: row["name"] for row in client.get("/api/admin/bots/summary").json()}
    assert names[bots[0].slug] == "S0"


def test_summary_drops_bot_after_delete_route(summary_env):
    client, bots = summary_env
    assert _slugs(client) == sorted(b.slug for b in bots)  # warm the cache

    r = client.delete(f"/api/admin/bots/{bots[0].slug}")
    assert r.status_code == 202, r.text

    assert _slugs(client) == [bots[1].slug]


def test_summary_drops_bot_soft_deleted_by_another_worker(summary_env):
    """Another worker's delete doesn't clear this process's cache; the
    live-id check must still catch it."""
    from models.bots import Bots
    from routes import admin_bots

    client, bots = summary_env
    assert _slugs(client) == sorted(b.slug for b in bots)  # warm the cache
    assert admin_bots._SUMMARY_CACHE, "summary should have been cached"

    Bots.soft_delete(bots[0].id)  # not via the route: cache is not cleared

    assert _slugs(client) == [bots[1].slug]


def test_summary_shows_bot_again_after_restore_by_another_worker(summary_env):
    from models.bots import Bots

    client, bots = summary_env
    Bots.soft_delete(bots[0].id)
    assert _slugs(client) == [bots[1].slug]  # cached without bot 0

    Bots.restore(bots[0].id)

    assert _slugs(client) == sorted(b.slug for b in bots)


def test_sessions_24h_counts_only_sessions_started_in_the_last_day(summary_env):
    """It used to count every session the bot ever had."""
    from datetime import datetime, timedelta

    from models.emly_sessions import EMLYSession

    client, bots = summary_env
    now = datetime.utcnow()
    for sid, started in (("old", now - timedelta(days=3)), ("recent", now - timedelta(hours=2))):
        EMLYSession.create(
            id=f"{bots[0].id}-{sid}", bot_id=bots[0].id, user_id="u", channel_id=None,
            started_at=started, last_activity_at=started, turn_count=1,
        )
    try:
        rows = {r["slug"]: r for r in client.get("/api/admin/bots/summary").json()}
        assert rows[bots[0].slug]["sessions_24h"] == 1
        assert rows[bots[1].slug]["sessions_24h"] == 0
    finally:
        EMLYSession.delete().where(EMLYSession.bot_id == bots[0].id).execute()
