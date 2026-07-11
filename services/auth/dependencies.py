"""FastAPI dependencies for the new admin-auth path.

The single export every route imports is ``get_admin`` — it pulls a token
out of the request (cookie preferred, ``Authorization: Bearer`` fallback),
verifies it through the configured ``AuthProvider``, resolves the
``Principal`` to an ``AdminUserModel`` row, and returns it.

Errors map to standard HTTP responses so the UI's session-expired
interceptor can redirect cleanly:

- 401 ``invalid_token``         — missing or unverifiable token.
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
    resolve_or_create_admin,
)

log = logging.getLogger(__name__)


def _cookie_name() -> str:
    return os.environ.get("AUTH_COOKIE_NAME", "emly_admin_session")


def _extract_token(request: Request) -> str | None:
    """Prefer cookie (browser path), fall back to ``Authorization: Bearer``
    (CI/scripts). Return ``None`` if neither is present."""
    cookie_token = request.cookies.get(_cookie_name())
    if cookie_token:
        return cookie_token
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if not auth_header:
        return None
    scheme, _, value = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()


def get_auth_provider() -> AuthProvider:
    """FastAPI ``Depends`` wrapper around the singleton accessor."""
    return _get_provider()


def get_admin(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
) -> AdminUserModel:
    token = _extract_token(request)
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
        admin = resolve_or_create_admin(principal)
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

    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "account_disabled"},
        )

    return admin


# -------- 401 body shape helpers (used by routes/auth.py for callback errors) --------


def make_401(code: str, **extra) -> JSONResponse:
    body = {"code": code}
    body.update(extra)
    return JSONResponse({"detail": body}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
