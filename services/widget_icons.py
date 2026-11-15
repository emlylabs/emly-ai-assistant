"""G10b: where the widget's admin-set icons may load from.

The widget mounts straight into the host page's DOM (no iframe), so the
host page's Content-Security-Policy governs its images, not Emly's. An
icon URL pointing at another host makes every visitor's browser contact
that host, which sends it their IP address and the page they're on.

``WIDGET_ICON_HOSTS`` lists the hosts icons may come from. When it is
set, each icon (logo, avatar, launcher open and close icons) must be:

- a path on the embedding site (``/logo.png``, ``./logo.png``);
- an inline ``data:image/`` URI; or
- an ``http(s)`` (or ``//``) URL on this app's own host
  (``APP_BASE_URL`` / ``PUBLIC_BASE_URL``) or on a listed host.

Entries are hosts with an optional port (``cdn.corp.example``,
``assets.internal:8443``) or ``*.example.com`` for subdomains. Unset
keeps today's behaviour: any http(s) host.

Enforced when a config is saved (rule R25, which can't be forced) and
again when the widget config is served, so an icon stored before the
list was set is dropped (the widget shows its default icon).
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

ICON_HOSTS_ENV = "WIDGET_ICON_HOSTS"

# (config path, label) for every admin-set image the widget loads.
ICON_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("widget.logo", "Logo"),
    ("widget.avatar", "Avatar"),
    ("open_icon", "Launcher icon"),
    ("close_icon", "Launcher close icon"),
)

_DEFAULT_PORTS = {"http": 80, "https": 443}


def icon_hosts() -> Optional[List[str]]:
    """Entries of WIDGET_ICON_HOSTS, or None when unset (any host)."""
    raw = os.environ.get(ICON_HOSTS_ENV)
    if raw is None or not raw.strip():
        return None
    return [e.strip().lower().rstrip(".") for e in raw.replace(",", " ").split() if e.strip()]


def _own_hosts() -> List[str]:
    out = []
    for name in ("APP_BASE_URL", "PUBLIC_BASE_URL"):
        parts = urlsplit((os.environ.get(name) or "").strip())
        if parts.hostname:
            port = parts.port or _DEFAULT_PORTS.get(parts.scheme.lower())
            out.append(f"{parts.hostname.lower()}:{port}" if port else parts.hostname.lower())
    return out


def _entry_allows(entry: str, host: str, port: int) -> bool:
    e_host, _, e_port = entry.partition(":")
    if e_port and (not e_port.isdigit() or int(e_port) != port):
        return False
    if e_host.startswith("*."):
        return host.endswith(e_host[1:]) and len(host) > len(e_host) - 1
    return host == e_host


def is_relative_or_inline(value: str) -> bool:
    v = value.strip()
    return v.startswith("data:image/") or (v.startswith(("/", "./", "../")) and not v.startswith("//"))


def is_icon_allowed(value: Optional[str]) -> bool:
    """Whether the browser may load this icon. True for every value when
    WIDGET_ICON_HOSTS is unset (format is checked separately)."""
    entries = icon_hosts()
    if entries is None or not value or is_relative_or_inline(value):
        return True
    v = value.strip()
    try:
        parts = urlsplit("https:" + v if v.startswith("//") else v)
        host, port = parts.hostname, parts.port
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not host:
        return False
    host = host.lower().rstrip(".")
    port = port or _DEFAULT_PORTS[scheme]
    return any(_entry_allows(e, host, port) for e in entries + _own_hosts())


def icon_host(value: str) -> str:
    """Host only, for messages."""
    v = value.strip()
    try:
        return urlsplit("https:" + v if v.startswith("//") else v).hostname or "?"
    except ValueError:
        return "?"
