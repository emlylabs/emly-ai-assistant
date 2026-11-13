"""Backend-for-Frontend (BFF) auth surface.

These endpoints exist **only** to mediate between the browser's cookie
and the IdP. The browser never sees a refresh_token, never sees a
signed id_token, and never has to make a same-origin /api/admin/* call
just to learn whether the session is still good — the BFF answers
that and refreshes the cookie in the background.

Endpoints
---------

- ``POST /api/auth/refresh`` — Read the encrypted refresh_token cookie,
  exchange it with the IdP for a fresh id_token, roll the cookie
  forward. Returns ``{ expires_in, expires_at }`` so the JS can
  reschedule the proactive-refresh timer. No body, no admin claims
  returned — this is a pure maintenance endpoint.

- ``POST /api/auth/refresh-claims`` — Same as ``/api/auth/refresh``,
  but additionally resolves the freshly-minted id_token to an
  ``AdminUser`` row and returns the full ``AdminUser`` shape. The UI
  uses this to update its ``me`` state after a permission change
  (e.g. the superadmin just granted you a new bot). Same
  Set-Cookie payload as ``/refresh``; the response body is the only
  difference.

Both endpoints accept the existing ``emly_admin_session`` httpOnly
cookie for CSRF protection (a missing cookie short-circuits to
``missing_session`` without contacting the IdP). They are intentionally
mounted **outside** the ``/api/admin/`` prefix so the embedded-issuer's
local-discovery loopback doesn't have to thread through any admin
middleware; the CSRF middleware (in ``services/auth/csrf.py``) has been
extended to cover ``/api/auth/`` so these endpoints still reject
cross-origin POSTs.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from services.audit import audit
from services.auth.base import AuthError, AuthProvider, InvalidTokenError
from services.auth.dependencies import get_auth_provider
from services.auth.factory import get_state_store
from services.auth.oidc import generate_nonce, generate_pkce_verifier, generate_state
from services.auth.ratelimit import limit_for, limiter
from services.secrets import decrypt_session_cookie, encrypt_session_cookie

log = logging.getLogger(__name__)

router = APIRouter()


# -------- cookie name helpers (mirror routes/auth.py) --------


def _cookie_name_session() -> str:
    return os.environ.get("AUTH_COOKIE_NAME", "emly_admin_session")


def _cookie_name_refresh() -> str:
    return os.environ.get("AUTH_REFRESH_COOKIE_NAME", "emly_admin_refresh")


def _cookie_name_expires_at() -> str:
    return os.environ.get("AUTH_EXPIRES_COOKIE_NAME", "session_expires_at")


def _cookie_secure(request: Request | None = None) -> bool:
    """See ``services.auth.cookie_policy.cookie_secure``."""
    from services.auth.cookie_policy import cookie_secure

    return cookie_secure(request)


def _cookie_samesite() -> str:
    from services.auth.cookie_policy import cookie_samesite

    return cookie_samesite()


def _cookie_domain() -> str | None:
    val = os.environ.get("AUTH_COOKIE_DOMAIN", "").strip()
    return val or None


def _max_age_seconds(expires_at: datetime) -> int:
    """Cookie max-age aligned to the id_token's remaining lifetime.
    Clamped to a minimum of 60s to avoid a 0-max-age cookie that some
    browsers immediately discard."""
    delta = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return max(delta, 60)


def _set_session_cookies(response: Response, request: Request, *, id_token: str, refresh_token: str | None, expires_at: datetime) -> None:
    """Set the trio of session cookies in one place.

    - ``emly_admin_session``: id_token (httpOnly, samesite=lax) — the
      BFF cookie the API endpoints read.
    - ``emly_admin_refresh``: encrypted refresh_token (httpOnly,
      samesite=lax) — the BFF secret used by /api/auth/refresh.
    - ``session_expires_at``: Unix timestamp of the id_token's expiry
      (public, samesite=lax, NOT httpOnly) — read by the JS
      proactive-refresh interceptor.
    """
    max_age = _max_age_seconds(expires_at)
    expires_at_unix = int(expires_at.timestamp())

    response.set_cookie(
        key=_cookie_name_session(),
        value=id_token,
        max_age=max_age,
        path="/",
        secure=_cookie_secure(request),
        httponly=True,
        samesite=_cookie_samesite(),
        domain=_cookie_domain(),
    )
    if refresh_token:
        response.set_cookie(
            key=_cookie_name_refresh(),
            value=encrypt_session_cookie(refresh_token) or "",
            max_age=max_age,
            path="/",
            secure=_cookie_secure(request),
            httponly=True,
            samesite=_cookie_samesite(),
            domain=_cookie_domain(),
        )
    response.set_cookie(
        key=_cookie_name_expires_at(),
        value=str(expires_at_unix),
        max_age=max_age,
        path="/",
        secure=_cookie_secure(request),
        httponly=False,
        samesite=_cookie_samesite(),
        domain=_cookie_domain(),
    )


def _clear_session_cookies(response: Response, request: Request) -> None:
    """Delete the trio of session cookies. Mirrors the attributes used
    on ``set_cookie`` so the browser actually drops them."""
    secure = _cookie_secure(request)
    samesite = _cookie_samesite()
    domain = _cookie_domain()
    for name in (_cookie_name_session(), _cookie_name_refresh(), _cookie_name_expires_at()):
        response.delete_cookie(
            key=name,
            path="/",
            secure=secure,
            samesite=samesite,
            domain=domain,
            httponly=(name != _cookie_name_expires_at()),
        )


# -------- helpers --------


def _extract_refresh_token(request: Request) -> str | None:
    """Read + decrypt the refresh-token cookie. Returns ``None`` if the
    cookie is absent, empty, or fails to decrypt (tampered / wrong
    key). The BFF treats any of these as "no usable refresh token"
    rather than 500-ing."""
    raw = request.cookies.get(_cookie_name_refresh())
    if not raw:
        return None
    return decrypt_session_cookie(raw)


async def _perform_refresh(request: Request, provider: AuthProvider, refresh_token: str):
    """Run the IdP refresh grant and update the session cookies.

    Returns the new ``TokenSet`` on success, ``None`` on failure (with
    cookies already cleared and a structured body the JS can use to
    decide between silent retry vs. full login redirect).
    """
    try:
        # The embedded issuer's token endpoint is sync. Hand it to the
        # threadpool so the loop stays responsive under contention.
        token_set = await run_in_threadpool(provider.refresh_tokens, refresh_token)
    except (InvalidTokenError, AuthError) as e:
        log.info("BFF refresh: IdP rejected refresh_token (%s) — clearing session", e)
        audit("auth.refresh_failed", request=request, success=False, payload={"err": str(e)})
        return None

    audit("auth.refresh", request=request, success=True)
    return token_set


# -------- endpoints --------


async def _refresh_response(
    request: Request,
    provider: AuthProvider,
    *,
    include_admin: bool,
) -> Response:
    """Shared body for /refresh and /refresh-claims.

    ``include_admin`` toggles whether the response carries the resolved
    ``AdminUser`` shape. Both endpoints roll the same cookies forward
    (the BFF contract is "a successful refresh = a refreshed session
    cookie, regardless of what the caller asked for").
    """
    refresh_token = _extract_refresh_token(request)
    if not refresh_token:
        # With offline_access disabled (Google) no refresh token is ever
        # issued, so this is the normal end of a session, not an anomaly:
        # don't fill the audit log. The UI stops its refresh loop on 401.
        from services.auth.factory import offline_access_requested

        if offline_access_requested():
            audit("auth.refresh_missing", request=request, success=False)
        return JSONResponse(
            {"detail": {"code": "missing_refresh_token"}},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token_set = await _perform_refresh(request, provider, refresh_token)
    if token_set is None:
        response = JSONResponse(
            {"detail": {"code": "refresh_failed"}},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        _clear_session_cookies(response, request)
        return response

    id_token = token_set.id_token or token_set.access_token
    # Some IdPs rotate the refresh_token on every refresh; some don't.
    # The OidcProvider surfaces the new one when present, otherwise
    # returns the input — so we always have a usable refresh_token
    # after a successful call.
    new_refresh = token_set.refresh_token
    expires_in = _max_age_seconds(token_set.expires_at)
    expires_at_unix = int(token_set.expires_at.timestamp())

    payload: dict = {
        "expires_in": expires_in,
        "expires_at": expires_at_unix,
    }

    if include_admin:
        # Resolve the freshly-minted id_token to the AdminUser row so
        # the UI can update its `me` state without a separate /me
        # round-trip. We re-use the same resolver the rest of the app
        # uses, but call it directly (without the FastAPI dep wrapper)
        # so the failure modes (AdminNotProvisioned, etc.) are
        # surfaces we control — a refresh-claims call should never
        # 401 the user out of the session just because the local
        # resolver is mid-migration.
        from models.admin_bot_memberships import AdminBotMemberships
        from services.auth.user_resolver import (
            AdminNotProvisioned,
            EmailLinkedToOtherProvider,
            EmailNotVerified,
            resolve_or_create_admin,
        )
        try:
            principal = provider.verify_token(id_token)
            admin = resolve_or_create_admin(principal)
            memberships = AdminBotMemberships.list_for_admin(admin.id)
            payload["admin"] = {
                "id": admin.id,
                "email": admin.email,
                "name": admin.name,
                "is_active": admin.is_active,
                "is_superadmin": admin.is_superadmin,
                "issuer": admin.issuer,
                "subject": admin.subject,
                "email_verified": admin.email_verified,
                "last_login_at": admin.last_login_at.isoformat() if admin.last_login_at else None,
                "memberships": [
                    {"bot_id": m.bot_id, "role": m.role} for m in memberships
                ],
            }
        except (AdminNotProvisioned, EmailLinkedToOtherProvider, EmailNotVerified):
            # The refresh minted a valid token, but the local
            # resolver can't activate / link this user right now.
            # Drop the admin field; the client can decide whether to
            # force a re-login based on the cookie + response shape.
            log.info("BFF refresh-claims: id_token valid but admin not resolvable")

    response = JSONResponse(payload, status_code=200)
    _set_session_cookies(
        response, request,
        id_token=id_token,
        refresh_token=new_refresh,
        expires_at=token_set.expires_at,
    )
    return response


@router.post("/api/auth/refresh", include_in_schema=False)
@limiter.limit(limit_for("auth_refresh"))
async def refresh(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
):
    """Mint a fresh id_token from the IdP and roll the session cookies
    forward. Returns the new ``expires_in`` / ``expires_at`` for the
    client to reschedule its proactive-refresh timer."""
    return await _refresh_response(request, provider, include_admin=False)


@router.post("/api/auth/refresh-claims", include_in_schema=False)
@limiter.limit(limit_for("auth_refresh_claims"))
async def refresh_claims(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
):
    """Force a token refresh and return the resolved ``AdminUser``.

    The UI calls this when the user clicks "Sign in again to load new
    bots" or otherwise wants to sync permission changes without
    bouncing through the IdP. Same cookie semantics as
    ``/api/auth/refresh``."""
    return await _refresh_response(request, provider, include_admin=True)
