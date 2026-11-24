"""Zoho, HubSpot and Salesforce facts the OAuth flow and the in-app guide share.

Hosts an admin can send us to are an allowlist. A token response that names
any other host is refused, so a poisoned row cannot turn a lead sync into
a request to an arbitrary server.
"""
from __future__ import annotations

import os
import re
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

# Salesforce. The token response has no ``expires_in``; the access token
# lives as long as the org's session timeout, which an admin can set as
# low as 15 minutes. We treat every token as good for 15 minutes and also
# refresh once if Salesforce rejects one sooner.
# https://help.salesforce.com/s/articleView?id=xcloud.remoteaccess_oauth_web_server_flow.htm
SALESFORCE_SCOPES = ("api", "refresh_token")
SALESFORCE_ENVIRONMENTS = (
    ("production", "Production or Developer Edition", "https://login.salesforce.com"),
    ("sandbox", "Sandbox", "https://test.salesforce.com"),
)
SALESFORCE_LOGIN = {env_id: host for env_id, _label, host in SALESFORCE_ENVIRONMENTS}
SALESFORCE_API_VERSION = "v66.0"
SALESFORCE_TOKEN_SECONDS = 900
# Required on a Lead, and the capture never asks for it.
SALESFORCE_NO_COMPANY = "[not provided]"
_SALESFORCE_LOGIN_HOSTS = {urlsplit(host).hostname for host in SALESFORCE_LOGIN.values()}
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

PROVIDERS = ("zoho", "hubspot", "salesforce")


def selected_providers(value) -> tuple | None:
    """The CRMs a form's ``trigger.crm_providers`` sends to.

    ``None`` (the key is absent) means every connected CRM, which is how
    forms behaved before the setting existed. A list, or a comma-separated
    string, names the CRMs; unknown names are dropped. Any other value is
    treated as "none" so a malformed setting never widens where leads go.
    """
    if value is None:
        return None
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return ()
    chosen = []
    for item in items:
        name = item.strip().lower() if isinstance(item, str) else ""
        if name in PROVIDERS and name not in chosen:
            chosen.append(name)
    return tuple(chosen)

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
    if provider == "salesforce":
        return " ".join(SALESFORCE_SCOPES)
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


def salesforce_login_server(environment: str) -> str:
    try:
        return SALESFORCE_LOGIN[(environment or "").strip().lower()]
    except KeyError:
        raise ValueError("unknown Salesforce environment") from None


def allow_salesforce_login(url: str) -> str:
    """The canonical login origin (production or sandbox), or raise."""
    parsed = urlsplit((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or host not in _SALESFORCE_LOGIN_HOSTS:
        raise ValueError("host is not a Salesforce login host")
    return f"https://{host}"


def allow_salesforce_instance(url: str) -> str:
    """The org's API origin from ``instance_url``, or raise.

    Each org has its own host, so this is a suffix rule rather than a list:
    https, a plain ``*.salesforce.com`` hostname (My Domain, sandbox, and
    older ``naNN`` instances), no credentials, port, query or path.
    """
    parsed = urlsplit((url or "").strip())
    host = (parsed.hostname or "").lower()
    labels = host.split(".")
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or not host.endswith(".salesforce.com")
        or len(labels) < 3
        or not all(_HOST_LABEL.match(label) for label in labels)
    ):
        raise ValueError("host is not a Salesforce instance")
    return f"https://{host}"


def _zoho_info() -> dict:
    redirect = oauth_redirect_uri("zoho")
    return {
        "id": "zoho",
        "name": "Zoho CRM",
        "data_center_label": "Data center",
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
        "data_center_label": "",
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


def _salesforce_info() -> dict:
    redirect = oauth_redirect_uri("salesforce")
    requirements = [
        "The org needs API access: Enterprise, Unlimited, Performance or Developer Edition, or Professional Edition with the API add-on.",
        "The person who clicks Connect needs permission to create and edit Leads.",
    ]
    if urlsplit(redirect).scheme != "https":
        requirements.append(
            "Salesforce requires an https callback URL except for localhost. Set PUBLIC_BASE_URL to the public https origin before connecting."
        )
    return {
        "id": "salesforce",
        "name": "Salesforce",
        "data_center_label": "Environment",
        "redirect_url": redirect,
        "scopes": list(SALESFORCE_SCOPES),
        "scope_help": "In the app's OAuth settings, select 'Manage user data via APIs (api)' and 'Perform requests at any time (refresh_token, offline_access)'.",
        "data_centers": [
            {"id": env_id, "label": label, "accounts_host": host}
            for env_id, label, host in SALESFORCE_ENVIRONMENTS
        ],
        "requirements": requirements,
        "steps": [
            "In Salesforce Setup, open External Client App Manager and create a new External Client App. (Orgs created before Spring '26 can still use an existing Connected App.)",
            "Enable OAuth. Set the Callback URL to the redirect URL shown here, exactly.",
            "Select the two OAuth scopes listed here. Keep 'Require Proof Key for Code Exchange (PKCE)' and 'Require secret for Web Server Flow' turned on; this app uses both.",
            "Set the refresh token policy to 'Refresh token is valid until revoked', or leads stop syncing when it expires.",
            "Copy the Consumer Key and Consumer Secret, paste them here as the client id and client secret, choose Production or Sandbox, and save. Then Connect.",
            "When a visitor submits an email, an open Lead with that email gets the new capture appended to Description. Otherwise a Lead is created with Last Name, First Name, Email, Phone, Description, and Company set to '" + SALESFORCE_NO_COMPANY + "' because Salesforce requires a company.",
        ],
    }


def catalog() -> dict:
    return {
        "public_base_url": public_base_url(),
        "public_base_configured": public_base_configured(),
        "providers": [_zoho_info(), _hubspot_info(), _salesforce_info()],
    }
