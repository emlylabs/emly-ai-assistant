"""Zoho CRM and Salesforce lead sync, with HTTP mocked.

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
from crm.oauth import ZohoOAuth
from crm.providers import (
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

    oauth = ZohoOAuth(client_factory=_client(handler))
    fresh = ScriptedSource(
        _record(expires_at=None, issued_at=datetime.utcnow(), expires_in=3600),
        lambda refresh_token: oauth.refresh(
            client_id="cid", client_secret="sec", refresh_token=refresh_token,
            accounts_server="https://accounts.zoho.com",
        ),
    )
    assert _run(RefreshingTokenProxy("conn-issued-fresh", fresh).access_token()) == "still-good"

    def refresh_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://accounts.zoho.com/oauth/v2/token")
        return httpx.Response(200, json={
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        })

    oauth = ZohoOAuth(client_factory=_client(refresh_handler))
    due = ScriptedSource(
        _record(
            access_token="old-access",
            expires_at=None,
            issued_at=datetime.utcnow() - timedelta(hours=2),
            expires_in=3600,
        ),
        lambda refresh_token: oauth.refresh(
            client_id="cid", client_secret="sec", refresh_token=refresh_token,
            accounts_server="https://accounts.zoho.com",
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

    zoho = _run(ZohoOAuth(client_factory=_client(zoho_handler)).exchange(
        client_id="cid", client_secret="sec", code="the-code",
        redirect_uri=oauth_redirect_uri("zoho"), accounts_server="https://accounts.zoho.com",
    ))
    assert zoho.refresh_token == "r"
    assert seen["zoho_url"].startswith("https://accounts.zoho.com/oauth/v2/token")
    assert "redirect_uri=https%3A%2F%2Fcrm.example.com%2Fcrm%2Fzoho%2Foauth%2Fcallback" in seen["zoho_body"]
    assert "grant_type=authorization_code" in seen["zoho_body"]


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
    with pytest.raises(CrmError):
        CrmProviderFactory().build(SimpleNamespace(id="1", provider="hubspot", api_domain=None))


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
    CrmConnections.upsert_credentials(bot.id, "salesforce", "cid", "super-secret", None)
    row = CrmConnections.get(bot.id, "salesforce")
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
    CrmConnections.upsert_credentials(bot.id, "salesforce", "cid", None, None)
    cleared = CrmConnections.get(bot.id, "salesforce")
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
    failed = CrmConnections.get(bot.id, "salesforce")
    assert failed.status == "error"
    assert failed.refresh_token_encrypted
    CrmConnections.mark_synced(cleared.id)
    restored = CrmConnections.get(bot.id, "salesforce")
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
    assert selected_providers(["Salesforce", "zoho", "salesforce", "pipedrive"]) == ("salesforce", "zoho")
    assert selected_providers("zoho, salesforce") == ("zoho", "salesforce")
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
        api_domain="https://www.zohoapis.com" if provider == "zoho" else "https://acme.my.salesforce.com",
    )


def test_syncable_connections_follow_the_form_selection(isolated_db):
    from models.bots import Bots
    from models.crm_connections import CrmConnections

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"crm-{uuid.uuid4().hex[:8]}", name="CRM")
    _connect(bot.id, "zoho")
    _connect(bot.id, "salesforce")

    def providers(selection):
        return sorted(row.provider for row in CrmConnections.list_syncable(bot.id, selection))

    assert providers(None) == ["salesforce", "zoho"]
    assert providers(("salesforce",)) == ["salesforce"]
    assert providers(("zoho",)) == ["zoho"]
    assert providers(()) == []
    assert CrmConnections.has_syncable(bot.id, ()) is False
    assert CrmConnections.has_syncable(bot.id, ("salesforce",)) is True


def test_facade_only_syncs_the_selected_crms(isolated_db):
    from crm.facade import LeadSyncFacade
    from models.bots import Bots

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"crm-{uuid.uuid4().hex[:8]}", name="CRM")
    _connect(bot.id, "zoho")
    _connect(bot.id, "salesforce")

    built = []

    class Factory:
        def build(self, row):
            built.append(row.provider)
            return object()

    class Strategy:
        async def sync(self, provider, lead):
            return None

    facade = LeadSyncFacade(factory=Factory(), strategy=Strategy())
    _run(facade.sync_bot(bot.id, _lead(), ("salesforce",)))
    assert built == ["salesforce"]
    built.clear()
    _run(facade.sync_bot(bot.id, _lead()))
    assert sorted(built) == ["salesforce", "zoho"]


def test_widget_reads_the_form_crm_selection_and_keeps_it_private(monkeypatch):
    from routes import widget

    forms = [
        {"Demo": {"form_schema": {"id": "demo_form", "name": "Demo"},
                  "trigger": {"crm_providers": ["salesforce"]}}},
        {"Callback": {"form_schema": {"id": "callback"}, "trigger": {}}},
        {"Quiet": {"form_schema": {"id": "quiet"}, "trigger": {"crm_providers": []}}},
    ]

    class FakeConfig:
        def model_dump(self, mode="json"):
            return {"c_forms_selected": forms}

    monkeypatch.setattr(widget, "get_config_for_bot", lambda _bot_id: FakeConfig())
    assert widget._form_crm_providers("bot", "demo_form") == ("salesforce",)
    assert widget._form_crm_providers("bot", "Demo") == ("salesforce",)
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


def test_widget_form_with_an_oddly_keyed_email_field_still_makes_a_lead():
    # The form builder's default key for a new field is ``field``.
    lead = lead_from_widget(
        {"form_title": "lead_capture_action",
         "action_payload": {"field": "ada@example.com", "form_title": "lead_capture_action"}}
    )
    assert lead is not None
    assert lead.email == "ada@example.com"
    assert lead.source == "widget:lead_capture_action"
    # A button click with no email anywhere still makes nothing.
    assert lead_from_widget({"action_payload": {"choice": "yes"}}) is None


# --------------------------------------------------------------------------
# Salesforce
# --------------------------------------------------------------------------

SF_INSTANCE = "https://acme.my.salesforce.com"
SF_API = "/services/data/v66.0"


def test_salesforce_instance_and_login_hosts_are_allowlisted():
    from crm.providers import allow_salesforce_instance, allow_salesforce_login

    assert allow_salesforce_instance("https://acme.my.salesforce.com") == SF_INSTANCE
    assert allow_salesforce_instance("https://acme--uat.sandbox.my.salesforce.com/") == "https://acme--uat.sandbox.my.salesforce.com"
    assert allow_salesforce_instance("https://na42.salesforce.com") == "https://na42.salesforce.com"
    for bad in (
        "http://acme.my.salesforce.com",
        "https://acme.my.salesforce.com.evil.example",
        "https://evil.example/.salesforce.com",
        "https://user:pw@acme.my.salesforce.com",
        "https://acme.my.salesforce.com:8443",
        "https://acme.my.salesforce.com/services",
        "https://salesforce.com",
        "https://a_b.my.salesforce.com",
    ):
        with pytest.raises(ValueError):
            allow_salesforce_instance(bad)
    assert allow_salesforce_login("https://test.salesforce.com/") == "https://test.salesforce.com"
    # My Domain logins (Developer Edition and friends) are accepted too.
    assert allow_salesforce_login("https://acme.my.salesforce.com") == "https://acme.my.salesforce.com"
    assert allow_salesforce_login("https://acme-dev-ed.develop.my.salesforce.com/") == "https://acme-dev-ed.develop.my.salesforce.com"
    for bad in (
        "https://na42.salesforce.com",
        "https://acme.lightning.force.com",
        "https://acme.evil.my.salesforce.com",
        "https://a.b.develop.my.salesforce.com",
        "https://my.salesforce.com",
        "https://acme.my.salesforce.com/services/oauth2/token",
        "https://acme.my.salesforce.com?x=1",
    ):
        with pytest.raises(ValueError):
            allow_salesforce_login(bad)


def test_salesforce_authorize_url_uses_pkce_s256_and_the_documented_scopes(monkeypatch):
    import base64
    import hashlib

    from crm.oauth import SalesforceOAuth, pkce_pair

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bots.example.com")
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    url = SalesforceOAuth().authorize_url(
        client_id="cid", redirect_uri=oauth_redirect_uri("salesforce"), state="st",
        accounts_server="https://login.salesforce.com", code_challenge=challenge,
    )
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "login.salesforce.com", "/services/oauth2/authorize")
    query = parse_qs(parts.query)
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["api refresh_token"]
    assert query["code_challenge"] == [challenge]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://bots.example.com/crm/salesforce/oauth/callback"]


def test_salesforce_code_exchange_sends_the_verifier_and_checks_instance_url():
    from crm.oauth import SalesforceOAuth

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://test.salesforce.com/services/oauth2/token"
        seen.update({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, json={
            "access_token": "sf-access", "refresh_token": "sf-refresh",
            "instance_url": seen.get("instance", SF_INSTANCE), "id": "https://login.salesforce.com/id/x/y",
            "issued_at": "1763800000000", "signature": "sig", "scope": "api refresh_token",
        })

    oauth = SalesforceOAuth(client_factory=_client(handler))
    grant = _run(oauth.exchange(
        client_id="cid", client_secret="sec", code="abc", redirect_uri="https://bots.example.com/cb",
        accounts_server="https://test.salesforce.com", code_verifier="v" * 50,
    ))
    assert seen["grant_type"] == "authorization_code"
    assert seen["code_verifier"] == "v" * 50
    assert seen["client_secret"] == "sec"
    assert grant.api_domain == SF_INSTANCE
    assert grant.refresh_token == "sf-refresh"
    assert grant.expires_in == 900  # Salesforce sends no expires_in

    def evil(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "instance_url": "https://evil.example"})

    with pytest.raises(CrmError):
        _run(SalesforceOAuth(client_factory=_client(evil)).exchange(
            client_id="cid", client_secret="sec", code="abc", redirect_uri="https://bots.example.com/cb",
            accounts_server="https://login.salesforce.com", code_verifier="v" * 50,
        ))


def test_salesforce_search_hit_appends_to_an_open_lead_description():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer access-token"
        path = _path(request)
        if request.method == "GET" and path == f"{SF_API}/query":
            q = request.url.params["q"]
            assert "FROM Lead WHERE Email = 'ada@example.com'" in q
            assert "IsConverted = false" in q
            return httpx.Response(200, json={"totalSize": 1, "done": True, "records": [
                {"attributes": {"type": "Lead"}, "Id": "00Q5g00000AbCdEFGH", "Description": "Called last week"}]})
        if request.method == "PATCH" and path == f"{SF_API}/sobjects/Lead/00Q5g00000AbCdEFGH":
            text = json.loads(request.content)["Description"]
            assert text.startswith("Called last week\n\n")
            assert "pricing" in text and "ada@example.com" in text
            return httpx.Response(204)
        raise AssertionError(f"unexpected {request.method} {path}")

    from crm.salesforce import SalesforceCrmAdapter

    adapter = SalesforceCrmAdapter(StaticToken(), SF_INSTANCE, client_factory=_client(handler))
    _run(UpsertLeadStrategy().sync(adapter, _lead()))


def test_salesforce_search_miss_creates_a_lead_with_the_required_fields():
    created = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = _path(request)
        if request.method == "GET" and path == f"{SF_API}/query":
            return httpx.Response(200, json={"totalSize": 0, "done": True, "records": []})
        if request.method == "POST" and path == f"{SF_API}/sobjects/Lead":
            created.update(json.loads(request.content))
            return httpx.Response(201, json={"id": "00Q5g00000AbCdEFGH", "success": True, "errors": []})
        raise AssertionError(f"unexpected {request.method} {path}")

    from crm.salesforce import SalesforceCrmAdapter

    adapter = SalesforceCrmAdapter(StaticToken(), SF_INSTANCE, client_factory=_client(handler))
    _run(UpsertLeadStrategy().sync(adapter, _lead()))
    assert created["LastName"] == "Lovelace"
    assert created["FirstName"] == "Ada"
    assert created["Company"] == "[not provided]"
    assert created["Email"] == "ada@example.com"
    assert created["Phone"] == "+15550100"
    assert "pricing" in created["Description"]


def test_salesforce_email_only_lead_and_quote_escaping():
    created = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = _path(request)
        if path.endswith("/query"):
            assert "Email = 'o\\'neil@example.com'" in request.url.params["q"]
            return httpx.Response(200, json={"records": []})
        created.update(json.loads(request.content))
        return httpx.Response(201, json={"id": "00Q5g00000AbCdE", "success": True})

    from crm.salesforce import SalesforceCrmAdapter

    adapter = SalesforceCrmAdapter(StaticToken(), SF_INSTANCE, client_factory=_client(handler))
    _run(UpsertLeadStrategy().sync(adapter, CapturedLead(email="o'neil@example.com")))
    assert created["LastName"] == "o'neil"
    assert "FirstName" not in created and "Phone" not in created


def test_salesforce_refreshes_once_when_a_token_is_rejected_early():
    calls = []

    class Tokens:
        async def access_token(self):
            return "stale"

        async def refresh_rejected(self, rejected):
            calls.append(rejected)
            return "fresh"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == "Bearer stale":
            return httpx.Response(401, json=[{"message": "Session expired or invalid", "errorCode": "INVALID_SESSION_ID"}])
        return httpx.Response(200, json={"records": []})

    from crm.salesforce import SalesforceCrmAdapter

    adapter = SalesforceCrmAdapter(Tokens(), SF_INSTANCE, client_factory=_client(handler))
    assert _run(adapter.search_by_email("ada@example.com")) is None
    assert calls == ["stale"]


def test_salesforce_errors_carry_the_error_code():
    def handler(request: httpx.Request) -> httpx.Response:
        if _path(request).endswith("/query"):
            return httpx.Response(200, json={"records": []})
        return httpx.Response(400, json=[{"message": "Use one of these records?", "errorCode": "DUPLICATES_DETECTED"}])

    from crm.salesforce import SalesforceCrmAdapter

    adapter = SalesforceCrmAdapter(StaticToken(), SF_INSTANCE, client_factory=_client(handler))
    with pytest.raises(CrmError, match="salesforce DUPLICATES_DETECTED"):
        _run(UpsertLeadStrategy().sync(adapter, _lead()))


def test_salesforce_record_ids_are_checked_before_they_reach_a_path():
    from crm.contracts import require_salesforce_id

    assert require_salesforce_id("00Q5g00000AbCdE") == "00Q5g00000AbCdE"
    assert require_salesforce_id("00Q5g00000AbCdEFGH") == "00Q5g00000AbCdEFGH"
    for bad in ("", "00Q/../../x", "00Q5g00000AbCdEF", "../" * 6):
        with pytest.raises(CrmError):
            require_salesforce_id(bad)


def test_proxy_refresh_rejected_reuses_a_token_another_thread_already_replaced():
    async def never(_):
        raise AssertionError("should not refresh")

    source = ScriptedSource(_record(access_token="newer"), never)
    proxy = RefreshingTokenProxy("conn-sf-1", source)
    assert _run(proxy.refresh_rejected("older")) == "newer"

    async def refresher(token):
        assert token == "old-refresh"
        return SimpleNamespace(access_token="brand-new", refresh_token=None, expires_in=900, api_domain=None)

    source = ScriptedSource(_record(access_token="older"), refresher)
    proxy = RefreshingTokenProxy("conn-sf-2", source)
    assert _run(proxy.refresh_rejected("older")) == "brand-new"
    assert source.saved.refresh_token == "old-refresh"  # no rotation: keep the old one


def test_salesforce_credentials_factory_and_callback(isolated_db, monkeypatch):
    from channels.auth.state import issue
    from crm.oauth import SalesforceOAuth, TokenGrant
    from crm.salesforce import SalesforceCrmAdapter
    from models.bots import Bots
    from models.crm_connections import CrmConnections
    from routes import crm_oauth
    from services.secrets import encrypt

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"crm-{uuid.uuid4().hex[:8]}", name="CRM")
    with pytest.raises(ValueError):
        CrmConnections.upsert_credentials(bot.id, "salesforce", "cid", "sec", "staging")
    CrmConnections.upsert_credentials(bot.id, "salesforce", "cid", "sec", "sandbox")
    row = CrmConnections.get(bot.id, "salesforce")
    assert (row.data_center, row.accounts_server, row.status) == ("sandbox", "https://test.salesforce.com", "pending")
    with pytest.raises(CrmError):
        CrmProviderFactory().build(row)  # no instance URL until connected

    seen = {}

    async def fake_exchange(self, **kwargs):
        seen.update(kwargs)
        return TokenGrant(access_token="sf-access", refresh_token="sf-refresh", expires_in=900, api_domain=SF_INSTANCE)

    monkeypatch.setattr(SalesforceOAuth, "exchange", fake_exchange)
    state = issue({"purpose": "crm", "bot_id": bot.id, "provider": "salesforce",
                   "redirect_to": f"/bots/{bot.slug}/crm", "pkce": encrypt("the-verifier")})
    response = _run(crm_oauth.crm_oauth_callback("salesforce", code="abc", state=state))
    assert response.headers["location"].endswith("crm=connected")
    assert seen["code_verifier"] == "the-verifier"
    assert seen["accounts_server"] == "https://test.salesforce.com"
    row = CrmConnections.get(bot.id, "salesforce")
    assert (row.status, row.api_domain) == ("connected", SF_INSTANCE)
    assert isinstance(CrmProviderFactory().build(row), SalesforceCrmAdapter)

    # A state without the verifier is refused and recorded.
    bare = issue({"purpose": "crm", "bot_id": bot.id, "provider": "salesforce", "redirect_to": f"/bots/{bot.slug}/crm"})
    response = _run(crm_oauth.crm_oauth_callback("salesforce", code="abc", state=bare))
    assert "crm_error=exchange" in response.headers["location"]
    assert CrmConnections.get(bot.id, "salesforce").last_error == "PKCE verifier is missing"


def test_salesforce_is_a_selectable_form_crm():
    from crm.providers import catalog, selected_providers

    assert selected_providers(["salesforce", "zoho"]) == ("salesforce", "zoho")
    info = {p["id"]: p for p in catalog()["providers"]}["salesforce"]
    assert [d["id"] for d in info["data_centers"]] == ["production", "sandbox", "developer"]
    assert [d["login_url_required"] for d in info["data_centers"]] == [False, False, True]
    assert info["data_center_label"] == "Environment"
    # The guide follows the External Client App form, section by section.
    titles = [section["title"] for section in info["sections"]]
    assert titles[:4] == ["Basic Information", "API (Enable OAuth Settings)", "Flow Enablement", "Security"]
    api_items = " ".join(info["sections"][1]["items"])
    assert "Manage user data via APIs (api)" in api_items
    assert "Perform requests at any time (refresh_token, offline_access)" in api_items
    assert {p["id"] for p in catalog()["providers"]} == {"zoho", "salesforce"}


def test_salesforce_developer_edition_saves_its_my_domain_login(isolated_db):
    from models.bots import Bots
    from models.crm_connections import CrmConnections, to_public

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"crm-{uuid.uuid4().hex[:8]}", name="CRM")
    with pytest.raises(ValueError):
        CrmConnections.upsert_credentials(bot.id, "salesforce", "cid", "sec", "developer")
    with pytest.raises(ValueError):
        CrmConnections.upsert_credentials(
            bot.id, "salesforce", "cid", "sec", "developer", "https://evil.example"
        )
    CrmConnections.upsert_credentials(
        bot.id, "salesforce", "cid", "sec", "developer", "https://Acme-Dev-Ed.develop.my.salesforce.com/"
    )
    row = CrmConnections.get(bot.id, "salesforce")
    assert row.data_center == "developer"
    assert row.accounts_server == "https://acme-dev-ed.develop.my.salesforce.com"
    assert to_public(row)["login_url"] == "https://acme-dev-ed.develop.my.salesforce.com"

    from crm.oauth import SalesforceOAuth

    url = SalesforceOAuth().authorize_url(
        client_id="cid", redirect_uri="https://bots.example.com/crm/salesforce/oauth/callback",
        state="s", accounts_server=row.accounts_server, code_challenge="c",
    )
    assert url.startswith("https://acme-dev-ed.develop.my.salesforce.com/services/oauth2/authorize?")
    # Production ignores a pasted URL.
    CrmConnections.upsert_credentials(
        bot.id, "salesforce", "cid", None, "production", "https://acme-dev-ed.develop.my.salesforce.com"
    )
    assert CrmConnections.get(bot.id, "salesforce").accounts_server == "https://login.salesforce.com"


def test_a_leftover_hubspot_row_is_not_listed_or_synced(isolated_db):
    from models.bots import Bots
    from models.crm_connections import CrmConnection, CrmConnections

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"crm-{uuid.uuid4().hex[:8]}", name="CRM")
    now = datetime.utcnow()
    # A row saved before HubSpot was removed.
    CrmConnection.create(
        id=str(uuid.uuid4()), bot_id=bot.id, provider="hubspot", status="connected",
        client_id="cid", refresh_token_encrypted="sealed", created_at=now, updated_at=now,
    )
    assert CrmConnections.list_for_bot(bot.id) == []
    assert CrmConnections.has_syncable(bot.id) is False
    with pytest.raises(ValueError):
        CrmConnections.upsert_credentials(bot.id, "hubspot", "cid", "sec", None)
