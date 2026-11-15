"""FastAPI dependencies for the new admin-auth path.

The single export every route imports is ``get_admin`` — it pulls a token
out of the request (the session cookie; ``Authorization: Bearer`` only when
``AUTH_ALLOW_BEARER=true``), verifies it through the configured ``AuthProvider``, resolves the
``Principal`` to an ``AdminUserModel`` row, and returns it.

Errors map to standard HTTP responses so the UI's session-expired
interceptor can redirect cleanly:

- 401 ``invalid_token``         — missing or unverifiable token.
- 401 ``bearer_disabled``       — only a Bearer header was sent, and Bearer
                                  access is off (Q-G3b: the default). A
                                  token copied out of a browser can't be
                                  replayed from a script.
- 401 ``not_provisioned``       — verified IdP user but no pending row /
                                  matching admin. UI shows "request access".
- 403 ``account_disabled``      — admin row exists but ``is_active=false``.
"""

from __future__ import annotations

import logging
import os

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from models.admin_users import AdminUserModel
from services.auth.base import AuthError, AuthProvider, InvalidTokenError
from services.auth.factory import get_auth_provider as _get_provider
from services.auth.user_resolver import (
    AdminNotProvisioned,
    EmailLinkedToOtherProvider,
    EmailNotVerified,
    resolve_or_create_admin,
)

log = logging.getLogger(__name__)


def _cookie_name() -> str:
    return os.environ.get("AUTH_COOKIE_NAME", "emly_admin_session")


BEARER_ENV = "AUTH_ALLOW_BEARER"


def bearer_allowed() -> bool:
    """Q-G3b: admin APIs accept ``Authorization: Bearer`` only when
    ``AUTH_ALLOW_BEARER=true`` (for scripts and CI). Off by default."""
    from utils.runtime import env_flag

    return env_flag(BEARER_ENV, default=False)


def _bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if not auth_header:
        return None
    scheme, _, value = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _extract_token(request: Request) -> str | None:
    """The session cookie (browser path), else a Bearer header when
    ``AUTH_ALLOW_BEARER`` is on. ``None`` if neither applies."""
    cookie_token = request.cookies.get(_cookie_name())
    if cookie_token:
        return cookie_token
    return _bearer_token(request) if bearer_allowed() else None


def get_auth_provider() -> AuthProvider:
    """FastAPI ``Depends`` wrapper around the singleton accessor."""
    return _get_provider()


def get_admin(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
) -> AdminUserModel:
    token = _extract_token(request)
    if not token and _bearer_token(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "bearer_disabled"},
            headers={"WWW-Authenticate": 'Bearer error="invalid_request"'},
        )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "missing_token"},
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    try:
        principal = provider.verify_token(token)
    except (InvalidTokenError, AuthError) as e:
        log.debug("Token verification failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token"},
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from e

    try:
        # Not a sign-in: don't write last_login_at on every request (G13f).
        admin = resolve_or_create_admin(principal, record_login=False)
    except AdminNotProvisioned as e:
        # 401 + structured body so the UI can route to the "Request access" page.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "not_provisioned", "email": principal.email},
        ) from e
    except EmailLinkedToOtherProvider as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "email_linked_to_other_provider"},
        ) from e
    except EmailNotVerified as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "email_not_verified", "email": principal.email},
        ) from e

    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "account_disabled"},
        )

    return admin


def audit_actor_id(request: Request, provider: AuthProvider, token: str | None = None) -> str | None:
    """Admin id to attribute an audit event to, on routes that run without
    a live session (logout, refresh). Uses ``token``, else the request's
    session token. The token must verify (signature, issuer, audience), but
    may be expired: that is why these routes run. The admin is looked up
    by issuer and subject, never created. ``None`` when there is no usable
    token; never raises."""
    try:
        token = token or _extract_token(request)
        if not token:
            return None
        principal = provider.verify_token(token, allow_expired=True)
        from models.admin_users import AdminUsers

        row = AdminUsers.get_by_issuer_subject(principal.issuer, principal.subject)
        return row.id if row else None
    except Exception:
        log.debug("audit actor not resolvable", exc_info=True)
        return None


# -------- 401 body shape helpers (used by routes/auth.py for callback errors) --------


def make_401(code: str, **extra) -> JSONResponse:
    body = {"code": code}
    body.update(extra)
    return JSONResponse({"detail": body}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
