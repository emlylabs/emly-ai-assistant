"""A7 follow-up: OTP lifetime comes from the workspace default
``otp_expires_in_minutes`` (1–30), falling back to 5 minutes."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def workspace_settings(isolated_db):
    from models.workspace_settings import WorkspaceSettingsStore

    original = WorkspaceSettingsStore.get().settings

    def _set(settings: dict) -> None:
        WorkspaceSettingsStore.put(settings, admin_id=None)

    yield _set
    WorkspaceSettingsStore.put(original, admin_id=None)


@pytest.mark.parametrize(
    "settings, expected",
    [
        ({}, 5),
        ({"otp_expires_in_minutes": 10}, 10),
        ({"otp_expires_in_minutes": 1}, 1),
        ({"otp_expires_in_minutes": 30}, 30),
        # Invalid values stored before validation existed fall back to 5.
        ({"otp_expires_in_minutes": 0}, 5),
        ({"otp_expires_in_minutes": 31}, 5),
        ({"otp_expires_in_minutes": "10"}, 5),
        ({"otp_expires_in_minutes": 2.5}, 5),
        ({"otp_expires_in_minutes": True}, 5),
    ],
)
def test_expiry_from_workspace_setting(workspace_settings, settings, expected):
    from services.otp import _otp_expires_in

    workspace_settings(settings)
    assert _otp_expires_in("any-bot") == expected


def test_issued_code_uses_workspace_expiry(workspace_settings, monkeypatch):
    from models.bots import Bots
    from models.otp_auth import OtpAuth, OtpAuthorization
    from services import otp as otp_mod

    workspace_settings({"otp_expires_in_minutes": 12})
    monkeypatch.setattr(otp_mod, "scheduler", type("S", (), {"add_job": lambda *a, **k: None})())
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"w-{uuid.uuid4().hex[:6]}", name="W")
    OtpAuth.delete().execute()
    try:
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        otp_mod.create_otp({"user_id": "a@example.com", "otp_type": "email"}, bot_id=bot.id)
        row = OtpAuthorization.get_otp(bot.id, "a@example.com")
        assert row["expires_in"] == 12
        delta = row["expires_at"] - before
        assert timedelta(minutes=11, seconds=55) < delta <= timedelta(minutes=12, seconds=5)
    finally:
        OtpAuth.delete().execute()


@pytest.fixture
def workspace_client(workspace_settings):
    from models.admin_users import AdminUsers
    from routes import workspace
    from services.auth.dependencies import get_admin

    superadmin = AdminUsers.insert(
        id=f"adm-{uuid.uuid4().hex[:6]}", email="root@example.com", is_active=True, is_superadmin=True
    )
    app = FastAPI()
    app.include_router(workspace.router, prefix="/api/admin")
    app.dependency_overrides[get_admin] = lambda: superadmin
    return TestClient(app)


@pytest.mark.parametrize("value", [0, 31, -5, 2.5, "10", True])
def test_put_rejects_invalid_expiry(workspace_client, value):
    r = workspace_client.put(
        "/api/admin/workspace/settings", json={"settings": {"otp_expires_in_minutes": value}}
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["key"] == "otp_expires_in_minutes"


def test_put_accepts_valid_expiry_and_omission(workspace_client):
    r = workspace_client.put("/api/admin/workspace/settings", json={"settings": {"otp_expires_in_minutes": 15}})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["otp_expires_in_minutes"] == 15
    r = workspace_client.put("/api/admin/workspace/settings", json={"settings": {}})
    assert r.status_code == 200, r.text
