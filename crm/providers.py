"""Zoho and HubSpot facts the OAuth flow and the in-app guide share.

Hosts an admin can send us to are an allowlist. A token response that names
any other host is refused, so a poisoned row cannot turn a lead sync into
a request to an arbitrary server.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

ZOHO_SCOPES = (
    "ZohoCRM.modules.leads.READ",
    "ZohoCRM.modules.leads.CREATE",
    "ZohoCRM.modules.leads.UPDATE",
    "ZohoSearch.securesearch.READ",
)

HUBSPOT_SCOPES = (
    "oauth",
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
    "crm.objects.leads.read",
    "crm.objects.leads.write",
)

# (id, label, accounts host). The client secret belongs to one data center.
ZOHO_DATA_CENTERS = (
    ("us", "United States", "https://accounts.zoho.com"),
    ("eu", "European Union", "https://accounts.zoho.eu"),
    ("in", "India", "https://accounts.zoho.in"),
    ("au", "Australia", "https://accounts.zoho.com.au"),
    ("jp", "Japan", "https://accounts.zoho.jp"),
    ("ca", "Canada", "https://accounts.zohocloud.ca"),
    ("sa", "Saudi Arabia", "https://accounts.zoho.sa"),
    ("cn", "China", "https://accounts.zoho.com.cn"),
)

ZOHO_ACCOUNTS = {dc_id: host for dc_id, _label, host in ZOHO_DATA_CENTERS}

ZOHO_API_DOMAINS = {
    "us": "https://www.zohoapis.com",
    "eu": "https://www.zohoapis.eu",
    "in": "https://www.zohoapis.in",
    "au": "https://www.zohoapis.com.au",
    "jp": "https://www.zohoapis.jp",
    "ca": "https://www.zohoapis.ca",
    "sa": "https://www.zohoapis.sa",
    "cn": "https://www.zohoapis.com.cn",
}

_ZOHO_ACCOUNT_HOSTS = {urlsplit(host).hostname for host in ZOHO_ACCOUNTS.values()}
_ZOHO_API_HOSTS = {urlsplit(host).hostname for host in ZOHO_API_DOMAINS.values()}

HUBSPOT_AUTHORIZE = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN = "https://api.hubapi.com/oauth/v3/token"
HUBSPOT_API = "https://api.hubapi.com"

# Lead to contact, HUBSPOT_DEFINED. A lead cannot be created without it.
# https://developers.hubspot.com/docs/guides/api/crm/objects/leads
HUBSPOT_LEAD_TO_CONTACT = 578

PROVIDERS = ("zoho", "hubspot")

_LOCAL_BASE = "http://localhost:8080"


def public_base_url() -> str:
    """The origin baked into the OAuth redirect. ``PUBLIC_BASE_URL`` wins."""
    for key in ("PUBLIC_BASE_URL", "APP_BASE_URL"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw.rstrip("/")
    return _LOCAL_BASE


def public_base_configured() -> bool:
    return bool((os.environ.get("PUBLIC_BASE_URL") or "").strip() or (os.environ.get("APP_BASE_URL") or "").strip())


def oauth_redirect_uri(provider: str) -> str:
    """The only redirect URI this app sends. The admin copies it; they do not type it."""
    if provider not in PROVIDERS:
        raise ValueError("unknown CRM")
    return f"{public_base_url()}/crm/{provider}/oauth/callback"


def scope_string(provider: str) -> str:
    if provider == "zoho":
        return ",".join(ZOHO_SCOPES)
    if provider == "hubspot":
        return " ".join(HUBSPOT_SCOPES)
    raise ValueError("unknown CRM")


def zoho_accounts_server(data_center: str) -> str:
    try:
        return ZOHO_ACCOUNTS[data_center]
    except KeyError:
        raise ValueError("unknown Zoho data center") from None


def zoho_api_domain_for_location(location: str) -> str:
    try:
        return ZOHO_API_DOMAINS[location.strip().lower()]
    except KeyError:
        raise ValueError("unknown Zoho location") from None


def _known_origin(url: str, known_hosts: set, canonical: dict) -> str:
    parsed = urlsplit((url or "").strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or host not in known_hosts
    ):
        raise ValueError("host is not an allowlisted Zoho host")
    for origin in canonical.values():
        if urlsplit(origin).hostname == host:
            return origin
    raise ValueError("host is not an allowlisted Zoho host")


def allow_zoho_accounts(url: str) -> str:
    """Return the canonical accounts origin, or raise. The caller's port and path are dropped."""
    return _known_origin(url, _ZOHO_ACCOUNT_HOSTS, ZOHO_ACCOUNTS)


def allow_zoho_api_domain(url: str) -> str:
    """Return the canonical API origin, or raise."""
    return _known_origin(url, _ZOHO_API_HOSTS, ZOHO_API_DOMAINS)


def _zoho_info() -> dict:
    redirect = oauth_redirect_uri("zoho")
    return {
        "id": "zoho",
        "name": "Zoho CRM",
        "redirect_url": redirect,
        "scopes": list(ZOHO_SCOPES),
        "scope_help": "Comma-separated. Paste them into the app's scope list.",
        "data_centers": [
            {"id": dc_id, "label": label, "accounts_host": host}
            for dc_id, label, host in ZOHO_DATA_CENTERS
        ],
        "requirements": [
            "Create a Server-based Application. The client secret only works in the data center where you created it.",
            "The redirect URL registered in Zoho must match the one on this page exactly.",
        ],
        "steps": [
            "Open https://api-console.zoho.com/ and create a Server-based Application in the same data center as the leads you want.",
            "Under Authorized Redirect URIs, paste the redirect URL shown here. Zoho compares it character for character.",
            "Allow these scopes: " + ", ".join(ZOHO_SCOPES) + ".",
            "Choose that data center on this page, paste the client id and client secret, and save. Then Connect.",
            "Zoho asks the admin to consent. Access tokens last one hour and this app refreshes them a minute before they expire. The refresh token lasts until it is revoked.",
            "When a visitor submits an email, a lead is created with Last name, Email, Phone and Description. If that email is already a lead, the new capture is appended to Description.",
        ],
    }


def _hubspot_info() -> dict:
    redirect = oauth_redirect_uri("hubspot")
    requirements = [
        "Lead scopes require Sales Hub Professional or Enterprise.",
        "The person who clicks Connect must be a Super Admin.",
    ]
    if urlsplit(redirect).scheme != "https":
        requirements.append(
            "HubSpot requires an https redirect URL in production. Set PUBLIC_BASE_URL to the public https origin before connecting."
        )
    return {
        "id": "hubspot",
        "name": "HubSpot",
        "redirect_url": redirect,
        "scopes": list(HUBSPOT_SCOPES),
        "scope_help": "Space-separated. HubSpot shows them as the app's scopes.",
        "data_centers": [],
        "requirements": requirements,
        "steps": [
            "In the HubSpot developer portal, create an app and set its redirect URL to the one shown here.",
            "Allow these scopes: " + ", ".join(HUBSPOT_SCOPES) + ".",
            "crm.objects.leads.read and crm.objects.leads.write are only available on Sales Hub Professional or Enterprise, and the installing user must be a Super Admin.",
            "Paste the client id and client secret on this page and save. Then Connect.",
            "Access tokens last 30 minutes. When HubSpot refreshes one it also returns a new refresh token, and this app stores that new token.",
            "A HubSpot lead has no Description field. A new email creates or reuses a contact, creates a lead linked to that contact, and adds a note. An email that already has a lead gets another note on that lead.",
        ],
    }


def catalog() -> dict:
    return {
        "public_base_url": public_base_url(),
        "public_base_configured": public_base_configured(),
        "providers": [_zoho_info(), _hubspot_info()],
    }
