"""Zoho and HubSpot lead sync, with HTTP mocked.

Covers upsert (search hit appends, search miss creates), token refresh when
the access token is due, and the authorize URLs.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from crm.capture import lead_from_slots, lead_from_widget
from crm.contracts import CapturedLead, CrmError
from crm.factory import CrmProviderFactory
from crm.http import make_client
from crm.hubspot import HubSpotCrmAdapter
from crm.oauth import HubSpotOAuth, ZohoOAuth
from crm.providers import (
    HUBSPOT_SCOPES,
    ZOHO_SCOPES,
    allow_zoho_accounts,
    allow_zoho_api_domain,
    oauth_redirect_uri,
)
from crm.strategy import UpsertLeadStrategy
from crm.token_proxy import RefreshingTokenProxy, TokenRecord, TokenSource
from crm.zoho import ZohoCrmAdapter


def _run(coro):
    return asyncio.run(coro)


def _client(handler):
    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return factory


class StaticToken:
    async def access_token(self):
        return "access-token"


def _lead() -> CapturedLead:
    return CapturedLead(
        email="ada@example.com",
        name="Ada Lovelace",
        phone="+15550100",
        use_case="pricing",
        source="widget:contact",
        captured_at=datetime(2026, 11, 18, 7, 0, 0),
    )


def _path(request: httpx.Request) -> str:
    return urlsplit(str(request.url)).path


class ScriptedSource(TokenSource):
    def __init__(self, record: TokenRecord, refresher):
        self.record = record
        self.refresher = refresher
        self.saved = None

    async def load(self) -> TokenRecord:
        return self.record

    async def refresh(self, refresh_token: str) -> TokenRecord:
        grant = await self.refresher(refresh_token)
        now = datetime.utcnow()
        return TokenRecord(
            access_token=grant.access_token,
            refresh_token=grant.refresh_token,
            issued_at=now,
            expires_in=grant.expires_in,
            expires_at=now + timedelta(seconds=grant.expires_in),
            api_domain=grant.api_domain,
        )

    async def save(self, record: TokenRecord) -> None:
        self.saved = record
        self.record = record


def _record(**overrides) -> TokenRecord:
    now = datetime.utcnow()
    base = dict(
        access_token="still-good",
        refresh_token="old-refresh",
        issued_at=now,
        expires_in=3600,
        expires_at=now + timedelta(hours=1),
    )
    base.update(overrides)
    return TokenRecord(**base)


def test_zoho_search_hit_appends_description():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Zoho-oauthtoken access-token"
        path = _path(request)
        if request.method == "GET" and path.endswith("/search"):
            assert request.url.params["email"] == "ada@example.com"
            return httpx.Response(200, json={"data": [{"id": "55", "Description": "Called last week"}]})
        if request.method == "PUT" and path == "/crm/v8/Leads/55":
            body = json.loads(request.content)
            text = body["data"][0]["Description"]
            assert "Called last week" in text
            assert "pricing" in text
            assert "ada@example.com" in text
            return httpx.Response(200, json={"data": [{"code": "SUCCESS", "details": {"id": "55"}}]})
        raise AssertionError(f"unexpected {request.method} {path}")

    adapter = ZohoCrmAdapter(StaticToken(), "https://www.zohoapis.com", client_factory=_client(handler))
    _run(UpsertLeadStrategy().sync(adapter, _lead()))


def test_zoho_search_miss_creates_the_lead():
    def handler(request: httpx.Request) -> httpx.Response:
        path = _path(request)
        if request.method == "GET" and path.endswith("/search"):
            return httpx.Response(204)
        if request.method == "POST" and path == "/crm/v8/Leads":
            body = json.loads(request.content)["data"][0]
            assert body["First_Name"] == "Ada"
            assert body["Last_Name"] == "Lovelace"
            assert body["Email"] == "ada@example.com"
            assert body["Phone"] == "+15550100"
            assert "pricing" in body["Description"]
            return httpx.Response(200, json={"data": [{"code": "SUCCESS", "status": "success", "details": {"id": "99"}}]})
        raise AssertionError(f"unexpected {request.method} {path}")

    adapter = ZohoCrmAdapter(StaticToken(), "https://www.zohoapis.com", client_factory=_client(handler))
    _run(UpsertLeadStrategy().sync(adapter, _lead()))


def test_hubspot_search_hit_adds_a_note_and_does_not_create_a_lead():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = _path(request)
        calls.append((request.method, path))
        assert request.headers["authorization"] == "Bearer access-token"
        if request.method == "POST" and path == "/crm/v3/objects/contacts/search":
            return httpx.Response(200, json={"results": [{"id": "10"}]})
        if request.method == "GET" and path == "/crm/v4/objects/contacts/10/associations/leads":
            return httpx.Response(200, json={"results": [{"toObjectId": 20}]})
        if request.method == "POST" and path == "/crm/v3/objects/notes":
            body = json.loads(request.content)
            assert "pricing" in body["properties"]["hs_note_body"]
            return httpx.Response(201, json={"id": "30"})
        if request.method == "PUT" and path == "/crm/v4/objects/notes/30/associations/default/leads/20":
            return httpx.Response(200, json={"status": "COMPLETE"})
        raise AssertionError(f"unexpected {request.method} {path}")

    adapter = HubSpotCrmAdapter(StaticToken(), client_factory=_client(handler))
    _run(UpsertLeadStrategy().sync(adapter, _lead()))
    assert ("POST", "/crm/v3/objects/leads") not in calls
    assert ("POST", "/crm/v3/objects/contacts") not in calls


def test_hubspot_search_miss_creates_contact_lead_and_note():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = _path(request)
        calls.append((request.method, path))
        if request.method == "POST" and path == "/crm/v3/objects/contacts/search":
            return httpx.Response(200, json={"total": 0, "results": []})
        if request.method == "POST" and path == "/crm/v3/objects/contacts":
            body = json.loads(request.content)
            assert body["properties"]["email"] == "ada@example.com"
            assert body["properties"]["firstname"] == "Ada"
            assert body["properties"]["lastname"] == "Lovelace"
            return httpx.Response(201, json={"id": "10"})
        if request.method == "GET" and path == "/crm/v4/objects/contacts/10/associations/leads":
            return httpx.Response(200, json={"results": []})
        if request.method == "POST" and path == "/crm/v3/objects/leads":
            body = json.loads(request.content)
            assert body["properties"]["hs_lead_name"] == "Ada Lovelace"
            assert body["associations"][0]["types"][0]["associationTypeId"] == 578
            assert body["associations"][0]["to"]["id"] == "10"
            return httpx.Response(201, json={"id": "20"})
        if request.method == "POST" and path == "/crm/v3/objects/notes":
            assert "pricing" in json.loads(request.content)["properties"]["hs_note_body"]
            return httpx.Response(201, json={"id": "30"})
        if request.method == "PUT" and path == "/crm/v4/objects/notes/30/associations/default/leads/20":
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected {request.method} {path}")

    adapter = HubSpotCrmAdapter(StaticToken(), client_factory=_client(handler))
    _run(UpsertLeadStrategy().sync(adapter, _lead()))
    assert ("POST", "/crm/v3/objects/contacts") in calls
    assert ("POST", "/crm/v3/objects/leads") in calls
    assert ("POST", "/crm/v3/objects/notes") in calls


def test_proxy_refreshes_a_due_zoho_token_and_keeps_the_refresh_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://accounts.zoho.com/oauth/v2/token")
        body = request.content.decode()
        assert "grant_type=refresh_token" in body
        assert "refresh_token=old-refresh" in body
        assert "refresh_token" in body and "new-refresh" not in body
        return httpx.Response(200, json={
            "access_token": "new-access",
            "expires_in": 3600,
            "api_domain": "https://www.zohoapis.com",
        })

    oauth = ZohoOAuth(client_factory=_client(handler))
    source = ScriptedSource(
        _record(access_token="old-access", expires_at=datetime.utcnow() - timedelta(minutes=5)),
        lambda refresh_token: oauth.refresh(
            client_id="cid", client_secret="sec", refresh_token=refresh_token,
            accounts_server="https://accounts.zoho.com",
        ),
    )
    token = _run(RefreshingTokenProxy("conn-zoho", source).access_token())
    assert token == "new-access"
    assert source.saved is not None
    assert source.saved.refresh_token == "old-refresh"
    assert source.saved.expires_in == 3600


def test_proxy_refreshes_inside_the_sixty_second_leeway():
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600,
                                          "api_domain": "https://www.zohoapis.com"})

    oauth = ZohoOAuth(client_factory=_client(handler))
    source = ScriptedSource(
        _record(expires_at=datetime.utcnow() + timedelta(seconds=30)),
        lambda refresh_token: oauth.refresh(
            client_id="cid", client_secret="sec", refresh_token=refresh_token,
            accounts_server="https://accounts.zoho.com",
        ),
    )
    token = _run(RefreshingTokenProxy("conn-leeway", source).access_token())
    assert called["n"] == 1
    assert token == "new-access"
    assert source.saved.refresh_token == "old-refresh"


def test_proxy_keeps_a_fresh_token():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("fresh token must not be refreshed")

    oauth = ZohoOAuth(client_factory=_client(handler))
    source = ScriptedSource(
        _record(expires_at=datetime.utcnow() + timedelta(minutes=10)),
        lambda refresh_token: oauth.refresh(
            client_id="cid", client_secret="sec", refresh_token=refresh_token,
            accounts_server="https://accounts.zoho.com",
        ),
    )
    assert _run(RefreshingTokenProxy("conn-fresh", source).access_token()) == "still-good"
    assert source.saved is None


def test_proxy_uses_issued_at_when_expires_at_is_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a token issued just now is not due")

    oauth = HubSpotOAuth(client_factory=_client(handler))
    fresh = ScriptedSource(
        _record(expires_at=None, issued_at=datetime.utcnow(), expires_in=3600),
        lambda refresh_token: oauth.refresh(
            client_id="cid", client_secret="sec", refresh_token=refresh_token,
        ),
    )
    assert _run(RefreshingTokenProxy("conn-issued-fresh", fresh).access_token()) == "still-good"

    def refresh_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.hubapi.com/oauth/v3/token"
        return httpx.Response(200, json={
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 1800,
        })

    oauth = HubSpotOAuth(client_factory=_client(refresh_handler))
    due = ScriptedSource(
        _record(
            access_token="old-access",
            expires_at=None,
            issued_at=datetime.utcnow() - timedelta(hours=2),
            expires_in=3600,
        ),
        lambda refresh_token: oauth.refresh(
            client_id="cid", client_secret="sec", refresh_token=refresh_token,
        ),
    )
    token = _run(RefreshingTokenProxy("conn-issued-due", due).access_token())
    assert token == "rotated-access"
    assert due.saved.refresh_token == "rotated-refresh"


def test_authorize_urls_carry_the_documented_scopes_and_zoho_offline_access(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://crm.example.com")
    monkeypatch.setenv("APP_BASE_URL", "https://ignored.example.com")
    zoho = ZohoOAuth().authorize_url(
        client_id="cid",
        redirect_uri=oauth_redirect_uri("zoho"),
        state="signed-state",
        accounts_server="https://accounts.zoho.eu",
    )
    parsed = urlsplit(zoho)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.zoho.eu"
    assert parsed.path == "/oauth/v2/auth"
    query = parse_qs(parsed.query)
    assert query["response_type"] == ["code"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["redirect_uri"] == ["https://crm.example.com/crm/zoho/oauth/callback"]
    assert set(query["scope"][0].split(",")) == set(ZOHO_SCOPES)
    assert "client_secret" not in zoho

    hubspot = HubSpotOAuth().authorize_url(
        client_id="cid",
        redirect_uri=oauth_redirect_uri("hubspot"),
        state="signed-state",
    )
    parsed = urlsplit(hubspot)
    assert parsed.netloc == "app.hubspot.com"
    assert parsed.path == "/oauth/authorize"
    query = parse_qs(parsed.query)
    assert query["redirect_uri"] == ["https://crm.example.com/crm/hubspot/oauth/callback"]
    assert set(query["scope"][0].split()) == set(HUBSPOT_SCOPES)


def test_app_base_url_is_the_redirect_when_public_base_url_is_unset(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com/")
    assert oauth_redirect_uri("zoho") == "https://app.example.com/crm/zoho/oauth/callback"


def test_code_exchange_posts_the_fixed_redirect(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://crm.example.com")
    seen = {}

    def zoho_handler(request: httpx.Request) -> httpx.Response:
        seen["zoho_url"] = str(request.url)
        seen["zoho_body"] = request.content.decode()
        return httpx.Response(200, json={
            "access_token": "a", "refresh_token": "r", "expires_in": 3600,
            "api_domain": "https://www.zohoapis.com",
        })

    def hubspot_handler(request: httpx.Request) -> httpx.Response:
        seen["hubspot_url"] = str(request.url)
        seen["hubspot_body"] = request.content.decode()
        return httpx.Response(200, json={
            "access_token": "a", "refresh_token": "r", "expires_in": 1800, "hub_id": 42,
        })

    zoho = _run(ZohoOAuth(client_factory=_client(zoho_handler)).exchange(
        client_id="cid", client_secret="sec", code="the-code",
        redirect_uri=oauth_redirect_uri("zoho"), accounts_server="https://accounts.zoho.com",
    ))
    hubspot = _run(HubSpotOAuth(client_factory=_client(hubspot_handler)).exchange(
        client_id="cid", client_secret="sec", code="the-code",
        redirect_uri=oauth_redirect_uri("hubspot"),
    ))
    assert zoho.refresh_token == "r" and hubspot.hub_id == "42"
    assert seen["zoho_url"].startswith("https://accounts.zoho.com/oauth/v2/token")
    assert seen["hubspot_url"] == "https://api.hubapi.com/oauth/v3/token"
    assert "redirect_uri=https%3A%2F%2Fcrm.example.com%2Fcrm%2Fzoho%2Foauth%2Fcallback" in seen["zoho_body"]
    assert "redirect_uri=https%3A%2F%2Fcrm.example.com%2Fcrm%2Fhubspot%2Foauth%2Fcallback" in seen["hubspot_body"]
    assert "grant_type=authorization_code" in seen["zoho_body"]
    assert "grant_type=authorization_code" in seen["hubspot_body"]


def test_zoho_hosts_are_allowlisted():
    assert allow_zoho_accounts("https://accounts.zoho.eu/oauth/v2/token") == "https://accounts.zoho.eu"
    assert allow_zoho_api_domain("https://www.zohoapis.eu/crm/v8/Leads") == "https://www.zohoapis.eu"
    with pytest.raises(ValueError):
        allow_zoho_accounts("https://accounts.zoho.eu.attacker.test")
    with pytest.raises(ValueError):
        allow_zoho_accounts("http://accounts.zoho.eu")
    with pytest.raises(ValueError):
        allow_zoho_api_domain("https://www.zohoapis.com.attacker.test")
    with pytest.raises(ValueError):
        allow_zoho_api_domain("http://www.zohoapis.com")
    with pytest.raises(ValueError):
        ZohoOAuth().authorize_url(
            client_id="cid", redirect_uri="https://crm.example.com/crm/zoho/oauth/callback",
            state="s", accounts_server="https://evil.example",
        )


def test_factory_is_a_singleton_and_refuses_zoho_without_an_api_domain():
    assert CrmProviderFactory.instance() is CrmProviderFactory.instance()
    with pytest.raises(CrmError):
        CrmProviderFactory().build(SimpleNamespace(id="1", provider="zoho", api_domain=None))
    built = CrmProviderFactory().build(SimpleNamespace(id="1", provider="hubspot"))
    assert isinstance(built, HubSpotCrmAdapter)


def test_widget_and_slot_captures_need_an_email():
    user = SimpleNamespace(email="stored@example.com", first_name="Stored", last_name="User", phone="1")
    assert lead_from_widget({"action_name": "click"}, user) is None
    lead = lead_from_widget(
        {"email": "ada@example.com", "first_name": "Ada", "action_payload": {"use_case": "pricing", "form_title": "contact"}},
        user,
    )
    assert lead is not None
    assert lead.email == "ada@example.com"
    assert lead.name == "Ada"
    assert lead.use_case == "pricing"
    from_profile = lead_from_widget({"email": "ada@example.com", "use_case": "pricing"}, user)
    assert from_profile is not None
    assert from_profile.name == "Stored User"
    assert lead.source.startswith("widget:")
    assert lead_from_slots({"order_id": "9"}, "chat") is None
    slotted = lead_from_slots(
        {"full_name": "Ada Lovelace", "email": "ada@example.com", "use_case": "pricing"}, "chat"
    )
    assert slotted is not None
    assert slotted.name == "Ada Lovelace"
    assert slotted.source == "chat"


def test_saved_secret_is_write_only_and_saving_again_clears_tokens(isolated_db):
    from services.secrets import decrypt

    from models.bots import Bots
    from models.crm_connections import CrmConnections, to_public

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"crm-{uuid.uuid4().hex[:8]}", name="CRM")
    CrmConnections.upsert_credentials(bot.id, "hubspot", "cid", "super-secret", None)
    row = CrmConnections.get(bot.id, "hubspot")
    assert decrypt(row.client_secret_encrypted) == "super-secret"
    public = to_public(row)
    assert public["has_secret"] is True
    assert public["status"] == "pending"
    assert "super-secret" not in json.dumps(public)
    assert "client_secret" not in public

    now = datetime.utcnow()
    CrmConnections.save_tokens(
        row.id, access_token="access", refresh_token="refresh", issued_at=now,
        expires_in=1800, expires_at=now + timedelta(seconds=1800),
    )
    assert CrmConnections.has_syncable(bot.id) is True
    CrmConnections.upsert_credentials(bot.id, "hubspot", "cid", None, None)
    cleared = CrmConnections.get(bot.id, "hubspot")
    assert cleared.status == "pending"
    assert cleared.access_token_encrypted is None
    assert cleared.refresh_token_encrypted is None
    assert decrypt(cleared.client_secret_encrypted) == "super-secret"
    assert CrmConnections.has_syncable(bot.id) is False
    CrmConnections.save_tokens(
        cleared.id, access_token="access", refresh_token="refresh", issued_at=now,
        expires_in=1800, expires_at=now + timedelta(seconds=1800),
    )
    CrmConnections.record_error(cleared.id, "zoho 401")
    failed = CrmConnections.get(bot.id, "hubspot")
    assert failed.status == "error"
    assert failed.refresh_token_encrypted
    CrmConnections.mark_synced(cleared.id)
    restored = CrmConnections.get(bot.id, "hubspot")
    assert restored.status == "connected"
    assert restored.last_error is None
    with pytest.raises(ValueError):
        CrmConnections.upsert_credentials(bot.id, "zoho", "cid", "sec", "mars")


def test_make_client_does_not_follow_redirects():
    client = make_client()
    try:
        assert client.follow_redirects is False
    finally:
        _run(client.aclose())


# --------------------------------------------------------------------------
# Per-form CRM selection (``trigger.crm_providers``)
# --------------------------------------------------------------------------


def test_form_crm_selection_parses_and_fails_closed():
    from crm.providers import selected_providers

    assert selected_providers(None) is None  # never chosen: every connected CRM
    assert selected_providers([]) == ()
    assert selected_providers(["HubSpot", "zoho", "hubspot", "salesforce"]) == ("hubspot", "zoho")
    assert selected_providers("zoho, hubspot") == ("zoho", "hubspot")
    assert selected_providers({"zoho": True}) == ()
    assert selected_providers(42) == ()


def _connect(bot_id: str, provider: str) -> None:
    from models.crm_connections import CrmConnections

    center = "us" if provider == "zoho" else None
    row_id = CrmConnections.upsert_credentials(bot_id, provider, "cid", "secret", center)
    now = datetime.utcnow()
    CrmConnections.save_tokens(
        row_id, access_token="access", refresh_token="refresh", issued_at=now,
        expires_in=1800, expires_at=now + timedelta(seconds=1800),
        api_domain="https://www.zohoapis.com" if provider == "zoho" else None,
    )


def test_syncable_connections_follow_the_form_selection(isolated_db):
    from models.bots import Bots
    from models.crm_connections import CrmConnections

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"crm-{uuid.uuid4().hex[:8]}", name="CRM")
    _connect(bot.id, "zoho")
    _connect(bot.id, "hubspot")

    def providers(selection):
        return sorted(row.provider for row in CrmConnections.list_syncable(bot.id, selection))

    assert providers(None) == ["hubspot", "zoho"]
    assert providers(("hubspot",)) == ["hubspot"]
    assert providers(("zoho",)) == ["zoho"]
    assert providers(()) == []
    assert CrmConnections.has_syncable(bot.id, ()) is False
    assert CrmConnections.has_syncable(bot.id, ("hubspot",)) is True


def test_facade_only_syncs_the_selected_crms(isolated_db):
    from crm.facade import LeadSyncFacade
    from models.bots import Bots

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"crm-{uuid.uuid4().hex[:8]}", name="CRM")
    _connect(bot.id, "zoho")
    _connect(bot.id, "hubspot")

    built = []

    class Factory:
        def build(self, row):
            built.append(row.provider)
            return object()

    class Strategy:
        async def sync(self, provider, lead):
            return None

    facade = LeadSyncFacade(factory=Factory(), strategy=Strategy())
    _run(facade.sync_bot(bot.id, _lead(), ("hubspot",)))
    assert built == ["hubspot"]
    built.clear()
    _run(facade.sync_bot(bot.id, _lead()))
    assert sorted(built) == ["hubspot", "zoho"]


def test_widget_reads_the_form_crm_selection_and_keeps_it_private(monkeypatch):
    from routes import widget

    forms = [
        {"Demo": {"form_schema": {"id": "demo_form", "name": "Demo"},
                  "trigger": {"crm_providers": ["hubspot"]}}},
        {"Callback": {"form_schema": {"id": "callback"}, "trigger": {}}},
        {"Quiet": {"form_schema": {"id": "quiet"}, "trigger": {"crm_providers": []}}},
    ]

    class FakeConfig:
        def model_dump(self, mode="json"):
            return {"c_forms_selected": forms}

    monkeypatch.setattr(widget, "get_config_for_bot", lambda _bot_id: FakeConfig())
    assert widget._form_crm_providers("bot", "demo_form") == ("hubspot",)
    assert widget._form_crm_providers("bot", "Demo") == ("hubspot",)
    assert widget._form_crm_providers("bot", "callback") is None
    assert widget._form_crm_providers("bot", "quiet") == ()
    assert widget._form_crm_providers("bot", "not-a-form") is None
    assert widget._form_crm_providers("bot", None) is None

    public = widget._scrub_cforms(forms)
    assert "crm_providers" not in public[0]["Demo"]["trigger"]


def test_a_form_with_no_crm_selected_starts_no_sync(monkeypatch):
    from crm import dispatch

    started = []
    monkeypatch.setattr(dispatch.threading, "Thread", lambda *a, **k: started.append(k) or SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(dispatch.CrmConnections, "has_syncable", lambda bot_id, providers=None: True)

    dispatch.schedule_widget_lead("bot", {"email": "ada@example.com"}, None, providers=())
    assert started == []
    dispatch.schedule_widget_lead("bot", {"email": "ada@example.com"}, None, providers=("zoho",))
    assert started and started[0]["args"][2] == ("zoho",)
