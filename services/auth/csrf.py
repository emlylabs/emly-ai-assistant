"""Origin-check middleware for cookie-authenticated mutating requests.

The admin UI authenticates via a ``SameSite=Strict`` cookie. ``SameSite``
already blocks most cross-site CSRF; this middleware adds belt-and-braces
``Origin`` validation so a leaked cookie can't be replayed from a third-party
page even via complex routing edge cases.

Bearer-only requests (no auth cookie present) bypass the check — without
ambient credentials, CSRF doesn't apply.

Configured via ``AUTH_CSRF_TRUSTED_ORIGINS`` (comma-separated). Defaults to
``APP_BASE_URL``. Mounted in ``main.py`` after CORS, before route handlers.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

log = logging.getLogger(__name__)

_PROTECTED_PATH_PREFIXES = ("/api/admin/",)
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Routes that legitimately need an unauthenticated POST (no cookie, by design).
# The login form posts into the embedded issuer with form-encoded creds — there
# is no session cookie at that point; CSRF doesn't apply.
_BYPASS_PATHS = ("/api/auth/local/authorize", "/api/auth/local/token", "/api/auth/local/logout")


def _trusted_origins() -> list[str]:
    raw = os.environ.get("AUTH_CSRF_TRUSTED_ORIGINS", "")
    explicit = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    if explicit:
        return explicit
    base = os.environ.get("APP_BASE_URL", "http://localhost:8080").rstrip("/")
    return [base]


def _cookie_name() -> str:
    return os.environ.get("AUTH_COOKIE_NAME", "emly_admin_session")


class OriginCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        method = request.method.upper()
        path = request.url.path
        if method not in _MUTATING_METHODS:
            return await call_next(request)
        if not any(path.startswith(p) for p in _PROTECTED_PATH_PREFIXES):
            return await call_next(request)
        if path in _BYPASS_PATHS:
            return await call_next(request)
        # Bearer-only callers (no cookie present) are CSRF-safe.
        if not request.cookies.get(_cookie_name()):
            return await call_next(request)

        origin = request.headers.get("origin") or request.headers.get("Origin")
        if origin is None:
            # Some clients (older Safari, native browsers on certain headers)
            # may omit Origin but always include Referer. Fall back to it.
            referer = request.headers.get("referer") or request.headers.get("Referer")
            if referer:
                parsed = urlparse(referer)
                if parsed.scheme and parsed.netloc:
                    origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin is None:
            log.warning("Rejecting cookie-auth %s %s: no Origin/Referer header", method, path)
            try:
                from services.audit import audit
                audit("csrf.origin_missing", request=request, success=False,
                      payload={"method": method, "path": path})
            except Exception:
                pass
            return JSONResponse(
                {"detail": {"code": "csrf_origin_missing"}},
                status_code=403,
            )
        origin_norm = origin.rstrip("/")
        if origin_norm not in _trusted_origins():
            log.warning(
                "Rejecting cookie-auth %s %s from untrusted origin %s",
                method, path, origin_norm,
            )
            try:
                from services.audit import audit
                audit("csrf.origin_rejected", request=request, success=False,
                      payload={"method": method, "path": path, "origin": origin_norm})
            except Exception:
                pass
            return JSONResponse(
                {"detail": {"code": "csrf_origin_rejected", "origin": origin_norm}},
                status_code=403,
            )
        return await call_next(request)


def install(app: ASGIApp) -> None:
    """Install the middleware on the given app. Idempotent."""
    # Starlette's add_middleware appends to the stack; callers ensure
    # idempotence at the call site.
    if not hasattr(app, "add_middleware"):
        raise RuntimeError("install() needs a FastAPI/Starlette app")
    app.add_middleware(OriginCheckMiddleware)
