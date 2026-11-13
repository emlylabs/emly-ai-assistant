"""Cookie attributes for the admin session cookies (C6).

Shared by ``routes/auth.py`` (login callback, logout) and
``routes/bff_auth.py`` (silent refresh) so the attributes a cookie is set
with always match the ones it is cleared with.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Request


def cookie_secure(request: Optional[Request] = None) -> bool:
    """Decide the ``Secure`` attribute.

    1. ``AUTH_COOKIE_SECURE=true|false`` wins.
    2. Otherwise Secure whenever the deployment is production-like
       (``MODE=prod``) or its public URL (``APP_BASE_URL``) is https. Behind
       a TLS-terminating proxy the app sees plain http unless the proxy is
       trusted (``FORWARDED_ALLOW_IPS``), so the request scheme alone would
       leave production cookies without Secure.
    3. Otherwise follow the request scheme (local development over http).

    Note: browsers accept Secure cookies on http://localhost (Chrome,
    Firefox), so local runs with an https APP_BASE_URL still work there;
    set AUTH_COOKIE_SECURE=false for anything else served over plain http.
    """
    explicit = os.environ.get("AUTH_COOKIE_SECURE")
    if explicit is not None and explicit.strip():
        return explicit.strip().lower() in ("1", "true", "yes", "on")
    from utils.runtime import is_prod_mode

    if is_prod_mode() or os.environ.get("APP_BASE_URL", "").strip().lower().startswith("https://"):
        return True
    if request is not None:
        return request.url.scheme.lower() == "https"
    return False


def cookie_samesite() -> str:
    """``lax`` by default, deliberately not ``strict``.

    The session cookie is set on the response to the IdP's cross-site
    redirect to ``/api/admin/auth/callback``. Browsers treat the follow-up
    redirect into the app as part of that cross-site chain, so a ``strict``
    cookie wouldn't be sent and the admin would bounce back to login. CSRF
    on mutating requests is covered separately by ``OriginCheckMiddleware``.
    ``AUTH_COOKIE_SAMESITE`` overrides.
    """
    val = os.environ.get("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    return val if val in ("strict", "lax", "none") else "lax"
