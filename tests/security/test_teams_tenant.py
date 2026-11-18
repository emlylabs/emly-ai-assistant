"""S15: a Teams bot answers only its own Microsoft 365 tenant.

Inbound verification was the Bot Framework JWT plus a serviceUrl prefix
check. The activity's tenant was never compared, and the default app type
is multi-tenant, so someone in another tenant who knew the app id could
sideload a manifest and chat with an internal bot. A serviceUrl prefix
without a trailing slash also matched look-alike hosts.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

from channels import teams
from channels.teams import TeamsAdapter, TeamsSecrets

OURS = "11111111-2222-3333-4444-555555555555"
THEIRS = "99999999-8888-7777-6666-555555555555"


@pytest.fixture(autouse=True)
def _jwt_ok(monkeypatch):
    async def ok(token, audience):
        return {"aud": audience}

    monkeypatch.setattr(teams, "verify_bot_framework_jwt", ok)


def _req(tenant=OURS, service_url="https://smba.trafficmanager.net/emea/"):
    body = {"type": "message", "serviceUrl": service_url, "channelData": {"tenant": {"id": tenant}},
            "conversation": {"id": "a:1", "conversationType": "personal", "tenantId": tenant}}
    req = Request({"type": "http", "method": "POST", "headers": [(b"authorization", b"Bearer t")]})
    req._body = json.dumps(body).encode()
    return req


def _verify(secrets, **kw):
    return asyncio.run(TeamsAdapter().verify_signature(_req(**kw), secrets))


@pytest.mark.parametrize("secrets", [
    TeamsSecrets(app_id="A", app_password="p", allowed_tenant_ids=[OURS.upper()]),
    TeamsSecrets(app_id="A", app_password="p", app_type="SingleTenant", tenant_id=OURS),
])
def test_only_the_allowed_tenant_gets_through(secrets):
    assert _verify(secrets, tenant=OURS) is True
    assert _verify(secrets, tenant=THEIRS) is False
    assert _verify(secrets, tenant="") is False


def test_without_a_tenant_any_is_accepted_and_logged_once(caplog):
    teams._UNRESTRICTED_WARNED.clear()
    secrets = TeamsSecrets(app_id="B", app_password="p")
    assert _verify(secrets, tenant=THEIRS) is True
    assert _verify(secrets, tenant=OURS) is True
    assert sum("any Microsoft 365 tenant" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.parametrize("prefix,url,ok", [
    ("https://smba.trafficmanager.net", "https://smba.trafficmanager.net/emea/", True),
    ("https://smba.trafficmanager.net", "https://smba.trafficmanager.net.evil.example/", False),
    ("https://smba.trafficmanager.net/", "https://smba.trafficmanager.net.evil.example/", False),
    ("http://smba.trafficmanager.net/", "http://smba.trafficmanager.net/x", False),
])
def test_service_url_prefix_matches_whole_segments(prefix, url, ok):
    assert teams._is_trusted_service_url(url, [prefix]) is ok
