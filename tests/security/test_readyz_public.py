"""S18: the public readiness probe says only whether the worker is ready.

/api/readyz returned raw exception text for database, Qdrant and model
failures (hosts, ports, driver errors) and the slugs of bots open to any
site or with broken configs, readable by any web page through CORS *. Each
call also ran a full bot scan and synchronous DB and Qdrant calls on the
event loop.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import main

    main._READYZ_CACHE.update(at=0.0, issues=None)
    monkeypatch.setattr(main, "_readiness",
                        lambda: [("db_unreachable", "could not connect to db.internal.corp:5432")])
    monkeypatch.setattr(main, "_collect_runtime_warnings",
                        lambda: [{"code": "widget_any_origin", "message": "1 bot(s): hr-secret-bot"}])
    yield TestClient(main.app)
    main._READYZ_CACHE.update(at=0.0, issues=None)


def test_public_probe_returns_codes_only(client):
    r = client.get("/api/readyz")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "issues": ["db_unreachable"]}
    assert "db.internal.corp" not in r.text and "hr-secret-bot" not in r.text


def test_probe_is_off_the_event_loop_and_cached():
    import main

    assert not inspect.iscoroutinefunction(main.readyz)
    calls = []
    main._READYZ_CACHE.update(at=0.0, issues=None)
    original = main._readiness
    main._readiness = lambda: calls.append(1) or []
    try:
        for _ in range(3):
            main.readyz()
    finally:
        main._readiness = original
        main._READYZ_CACHE.update(at=0.0, issues=None)
    assert calls == [1]


def test_details_are_for_superadmins(client, isolated_db):
    import main
    from models.admin_users import AdminUsers
    from services.auth.dependencies import get_admin

    plain = AdminUsers.insert(id="adm-p", email="p@x.example", is_active=True)
    root = AdminUsers.insert(id="adm-r", email="r@x.example", is_active=True, is_superadmin=True)
    try:
        main.app.dependency_overrides[get_admin] = lambda: plain
        assert client.get("/api/admin/system/status").status_code == 403
        main.app.dependency_overrides[get_admin] = lambda: root
        body = client.get("/api/admin/system/status").json()
        assert body["issues"] == ["db_unreachable: could not connect to db.internal.corp:5432"]
        assert body["warnings"][0]["code"] == "widget_any_origin"
    finally:
        main.app.dependency_overrides.pop(get_admin, None)
