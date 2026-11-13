"""Security response headers (B6).

Every response gets:

* ``X-Content-Type-Options: nosniff``
* ``Referrer-Policy: strict-origin-when-cross-origin``
* ``Strict-Transport-Security`` when the public URL (``PUBLIC_BASE_URL`` or
  ``APP_BASE_URL``) is https

Everything except the widget surfaces also gets anti-framing headers —
``X-Frame-Options: DENY`` and ``Content-Security-Policy: frame-ancestors
'none'`` — so the admin UI, the admin API and the built-in sign-in form
can't be clickjacked. The widget surfaces are exempt: ``/widget/*`` (its
API), ``/emly-widget.js`` (the embed script customers load) and
``/widget-test.html`` (the hosted preview page). The widget renders into the
host page's DOM rather than an iframe, so it needs no framing permission,
but nothing here should constrain how customers embed it.

Implemented as plain ASGI (not ``BaseHTTPMiddleware``) so streaming
responses — chat NDJSON and the admin SSE feed — pass through untouched.
A header a route already set is never overwritten; an existing CSP without
``frame-ancestors`` gets the directive appended.
"""

from __future__ import annotations

import os
from typing import Iterable
from urllib.parse import urlparse

HSTS_VALUE = "max-age=31536000"
_FRAME_ANCESTORS = "frame-ancestors 'none'"

# Paths that make up the embeddable widget; no framing rules applied.
_WIDGET_PREFIXES = ("/widget/",)
_WIDGET_PATHS = frozenset({"/emly-widget.js", "/widget-test.html"})


def is_widget_path(path: str) -> bool:
    return path in _WIDGET_PATHS or path.startswith(_WIDGET_PREFIXES)


def hsts_enabled() -> bool:
    for name in ("PUBLIC_BASE_URL", "APP_BASE_URL"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return urlparse(value).scheme == "https"
    return False


def _has(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bool:
    return any(k.lower() == name for k, _ in headers)


def security_headers_for(path: str, existing: list[tuple[bytes, bytes]], *, hsts: bool) -> list[tuple[bytes, bytes]]:
    """Return ``existing`` plus the security headers for ``path``."""
    headers = list(existing)

    def add(name: str, value: str) -> None:
        if not _has(headers, name.lower().encode()):
            headers.append((name.lower().encode(), value.encode()))

    add("X-Content-Type-Options", "nosniff")
    add("Referrer-Policy", "strict-origin-when-cross-origin")
    if hsts:
        add("Strict-Transport-Security", HSTS_VALUE)

    if not is_widget_path(path):
        add("X-Frame-Options", "DENY")
        csp_index = next(
            (i for i, (k, _) in enumerate(headers) if k.lower() == b"content-security-policy"), None
        )
        if csp_index is None:
            headers.append((b"content-security-policy", _FRAME_ANCESTORS.encode()))
        else:
            name, value = headers[csp_index]
            if b"frame-ancestors" not in value.lower():
                headers[csp_index] = (name, value.rstrip(b"; ") + b"; " + _FRAME_ANCESTORS.encode())
    return headers


class SecurityHeadersMiddleware:
    def __init__(self, app) -> None:
        self.app = app
        # Read once at startup; base URLs don't change at runtime.
        self.hsts = hsts_enabled()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = security_headers_for(
                    path, list(message.get("headers", [])), hsts=self.hsts
                )
            await send(message)

        await self.app(scope, receive, send_with_headers)
