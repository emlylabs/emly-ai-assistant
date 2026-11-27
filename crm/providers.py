"""Zoho CRM and Salesforce facts the OAuth flow and the in-app guide share.

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

# Salesforce. The token response has no ``expires_in``; the access token
# lives as long as the org's session timeout, which an admin can set as
# low as 15 minutes. We treat every token as good for 15 minutes and also
# refresh once if Salesforce rejects one sooner.
# https://help.salesforce.com/s/articleView?id=xcloud.remoteaccess_oauth_web_server_flow.htm
SALESFORCE_SCOPES = ("api", "refresh_token")
# (id, label, login host). "developer" has no fixed host: the admin pastes
# the org's My Domain URL, e.g. https://acme-dev-ed.develop.my.salesforce.com.
SALESFORCE_ENVIRONMENTS = (
    ("production", "Production", "https://login.salesforce.com"),
    ("sandbox", "Sandbox", "https://test.salesforce.com"),
    ("developer", "Developer Edition (My Domain URL)", ""),
)
SALESFORCE_MY_DOMAIN_ENVIRONMENT = "developer"
SALESFORCE_LOGIN = {env_id: host for env_id, _label, host in SALESFORCE_ENVIRONMENTS if host}
# What sits between the org's name and ``my.salesforce.com`` in a My Domain
# login host: nothing (production), develop (Developer Edition), sandbox,
# scratch, or trailblaze (Trailhead Playground).
_MY_DOMAIN_KINDS = ("", "develop", "sandbox", "scratch", "trailblaze")
SALESFORCE_API_VERSION = "v66.0"
SALESFORCE_TOKEN_SECONDS = 900
# Required on a Lead, and the capture never asks for it.
SALESFORCE_NO_COMPANY = "[not provided]"
_SALESFORCE_LOGIN_HOSTS = {urlsplit(host).hostname for host in SALESFORCE_LOGIN.values()}
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

PROVIDERS = ("zoho", "salesforce")


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


def salesforce_login_server(environment: str, login_url: str | None = None) -> str:
    """The login origin for an environment. Developer Edition uses the org's My Domain URL."""
    env = (environment or "").strip().lower()
    if env == SALESFORCE_MY_DOMAIN_ENVIRONMENT:
        if not (login_url or "").strip():
            raise ValueError("Paste your org's My Domain URL, e.g. https://yourorg-dev-ed.develop.my.salesforce.com")
        return allow_salesforce_my_domain(login_url)
    try:
        return SALESFORCE_LOGIN[env]
    except KeyError:
        raise ValueError("unknown Salesforce environment") from None


def _plain_https_host(url: str) -> str:
    """The lowercase host of a bare https origin (a trailing ``/`` is fine), or ""."""
    parsed = urlsplit((url or "").strip())
    try:
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        return ""
    host = (parsed.hostname or "").lower()
    if not host or not all(_HOST_LABEL.match(label) for label in host.split(".")):
        return ""
    return host


def _is_my_domain(host: str) -> bool:
    if not host.endswith(".my.salesforce.com"):
        return False
    middle = host[: -len(".my.salesforce.com")].split(".")
    # ``name`` or ``name.kind``; the org's name is the first label.
    if len(middle) == 1:
        return True
    return len(middle) == 2 and middle[1] in _MY_DOMAIN_KINDS


def allow_salesforce_my_domain(url: str) -> str:
    """A My Domain login origin (``https://<name>[.develop|.sandbox|...].my.salesforce.com``), or raise.

    The admin types this one, and the client secret is posted to it, so
    only the shapes Salesforce issues are accepted.
    """
    host = _plain_https_host(url)
    if not host or not _is_my_domain(host):
        raise ValueError(
            "That is not a Salesforce My Domain URL. It looks like https://yourorg-dev-ed.develop.my.salesforce.com"
        )
    return f"https://{host}"


def allow_salesforce_login(url: str) -> str:
    """The canonical login origin (login., test., or a My Domain host), or raise."""
    host = _plain_https_host(url)
    if host in _SALESFORCE_LOGIN_HOSTS or (host and _is_my_domain(host)):
        return f"https://{host}"
    raise ValueError("host is not a Salesforce login host")


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


SALESFORCE_SCOPE_LABELS = (
    "Manage user data via APIs (api)",
    "Perform requests at any time (refresh_token, offline_access)",
)


def _salesforce_info() -> dict:
    redirect = oauth_redirect_uri("salesforce")
    requirements = [
        "The org needs API access: Enterprise, Unlimited, Performance or Developer Edition, or Professional Edition with the API add-on.",
        "The person who clicks Connect needs the API Enabled permission and permission to create and edit Leads.",
    ]
    if urlsplit(redirect).scheme != "https":
        requirements.append(
            "Salesforce requires an https callback URL except for localhost. Set PUBLIC_BASE_URL to the public https origin before connecting."
        )
    # One block per section of Setup > External Client App Manager > New
    # External Client App, in the order the form shows them.
    sections = [
        {
            "title": "Basic Information",
            "items": [
                "External Client App Name: any name, e.g. Emly leads. API Name fills itself in.",
                "Contact Email: your email. Distribution State: Local.",
                "Contact Phone, Info URL, Logo, Icon and Description are optional.",
            ],
        },
        {
            "title": "API (Enable OAuth Settings)",
            "items": [
                "Tick Enable OAuth.",
                "Callback URL: paste the redirect URL shown below, exactly.",
                "OAuth Scopes: move these two to Selected OAuth Scopes: "
                + " and ".join(f"'{label}'" for label in SALESFORCE_SCOPE_LABELS)
                + ". No other scope is needed.",
                "Leave 'Introspect all Tokens' and 'Configure ID token' unticked.",
            ],
        },
        {
            "title": "Flow Enablement",
            "items": [
                "Leave every box unticked. This app uses the authorization code (web server) flow, which needs no box here.",
            ],
        },
        {
            "title": "Security",
            "items": [
                "Tick 'Require secret for Web Server Flow' and 'Require secret for Refresh Token Flow'. This app always sends the secret.",
                "'Require Proof Key for Code Exchange (PKCE)' stays on. This app sends a PKCE challenge.",
                "'Enable Refresh Token Rotation' stays on. This app saves the new refresh token Salesforce returns on every refresh.",
                "'Limit Idle Refresh Token Time-to-Live (TTL) to 30 Days' stays on. If no lead reaches Salesforce for 30 days, the connection expires and shows an error here; click Connect again.",
                "Leave 'Issue JSON Web Token (JWT)-based access tokens', 'Enforce Refresh Token IP Allowlist' and 'Require Demonstrating Proof of Possession (DPoP)' unticked.",
            ],
        },
        {
            "title": "After you click Create",
            "items": [
                "Open the app, go to the Settings tab, and under OAuth Settings click 'Consumer Key and Secret'. Salesforce emails you a code to view them.",
                "Paste the Consumer Key as the client id and the Consumer Secret as the client secret below.",
                "If the app's Policies tab limits Permitted Users to approved users, add the person who will click Connect.",
                "A new app can take up to 10 minutes before sign-in works.",
            ],
        },
        {
            "title": "On this page",
            "items": [
                "Environment: Production signs in at login.salesforce.com, Sandbox at test.salesforce.com. For a Developer Edition org, choose Developer Edition and paste its My Domain URL (Setup > My Domain, e.g. https://yourorg-dev-ed.develop.my.salesforce.com).",
                "Save, then Connect, and approve access in Salesforce.",
                "When a visitor submits an email, an open Lead with that email gets the new capture added to Description. Otherwise a Lead is created with First Name, Last Name, Email, Phone, Description, and Company set to '"
                + SALESFORCE_NO_COMPANY
                + "' because Salesforce requires a company.",
            ],
        },
    ]
    return {
        "id": "salesforce",
        "name": "Salesforce",
        "data_center_label": "Environment",
        "redirect_url": redirect,
        "scopes": list(SALESFORCE_SCOPES),
        "scope_help": "In the app's OAuth Scopes, select "
        + " and ".join(f"'{label}'" for label in SALESFORCE_SCOPE_LABELS)
        + ".",
        "data_centers": [
            {
                "id": env_id,
                "label": label,
                "accounts_host": host,
                "login_url_required": env_id == SALESFORCE_MY_DOMAIN_ENVIRONMENT,
            }
            for env_id, label, host in SALESFORCE_ENVIRONMENTS
        ],
        "requirements": requirements,
        "steps": [],
        "sections": sections,
    }


def catalog() -> dict:
    return {
        "public_base_url": public_base_url(),
        "public_base_configured": public_base_configured(),
        "providers": [_zoho_info(), _salesforce_info()],
    }
