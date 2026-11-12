"""Admin OIDC client routes.

The admin UI hits ``/api/admin/auth/login`` to start the flow. The route
generates ``state`` / ``code_verifier`` / ``nonce``, stashes them server-side,
and 302s to the configured IdP's authorize URL. The IdP redirects back to
``/api/admin/auth/callback?code=...&state=...``; we exchange the code, verify
the resulting id_token's nonce, resolve/create the admin row, and set the
BFF session-cookie trio (``emly_admin_session`` id_token,
``emly_admin_refresh`` encrypted refresh_token, and the public
``session_expires_at`` Unix timestamp). Logout clears all three and
surfaces the IdP's end-session URL so the UI can redirect there.

This file is provider-agnostic. Whether ``AUTH_OIDC_ISSUER`` points at the
embedded issuer (``${APP_BASE_URL}``) or at Auth0/Clerk/Keycloak/Cognito is
invisible here — ``OidcProvider`` handles the dance.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from services.audit import audit
from services.auth.base import (
    AuthError,
    AuthProvider,
    InvalidTokenError,
    OAuthState,
)
from services.auth.dependencies import get_admin, get_auth_provider
from services.auth.factory import get_state_store
from services.auth.oidc import generate_nonce, generate_pkce_verifier, generate_state
from services.auth.ratelimit import limit_for, limiter
from services.secrets import encrypt_session_cookie

log = logging.getLogger(__name__)

router = APIRouter()


# -------- config helpers --------


def _app_base_url(request: Request | None = None) -> str:
    """Public-facing base URL of this app.

    Resolution order:
      1. ``APP_BASE_URL`` env (the operator's contract — set this in prod).
      2. The incoming request's scheme + host. uvicorn runs with
         ``--forwarded-allow-ips '*'`` (see ``start.sh``), so behind a
         reverse proxy ``request.url`` already reflects ``X-Forwarded-Proto``
         / ``X-Forwarded-Host``.
      3. ``http://localhost:8080`` — last-resort fallback for code paths
         that don't have a request in scope.
    """
    explicit = os.environ.get("APP_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    if request is not None:
        # Starlette exposes request.url.netloc which already includes the
        # port when non-standard. Scheme reflects the proxy's
        # X-Forwarded-Proto when uvicorn trusts forwarded headers.
        netloc = request.url.netloc or request.headers.get("host")
        if netloc:
            return f"{request.url.scheme}://{netloc}".rstrip("/")
    return "http://localhost:8080"


_TENANT_CALLBACK_PATH = "/api/admin/auth/callback"


def _redirect_uri(request: Request | None = None) -> str:
    """The ``redirect_uri`` we send to the IdP and re-send on token exchange.

    When ``AUTH_OIDC_REDIRECT_URI`` is set (the centralized-gateway pattern
    used by ``app-deployer``), every tenant subdomain shares a single
    redirect URI registered with the IdP — the gateway later routes the
    callback back to this tenant by parsing the state envelope. Without it
    we fall back to per-tenant ``{app_base}/api/admin/auth/callback``.
    """
    gateway_uri = os.environ.get("AUTH_OIDC_REDIRECT_URI", "").strip()
    if gateway_uri:
        return gateway_uri.rstrip("/") if gateway_uri.endswith("/") else gateway_uri
    return f"{_app_base_url(request)}{_TENANT_CALLBACK_PATH}"


def _gateway_hmac_secret() -> str | None:
    val = os.environ.get("AUTH_GATEWAY_HMAC_SECRET", "").strip()
    return val or None


def _tenant_host(request: Request | None = None) -> str:
    """Bare host of this tenant (no scheme, no port unless non-default).

    Read in priority order from ``PUBLIC_BASE_URL`` / ``APP_BASE_URL`` (the
    operator's contract) and finally from the inbound request's Host. The
    gateway parser refuses hosts containing ``:`` / ``/`` / ``?`` / ``#`` /
    ``@`` / ``\\`` so we strip the scheme and any path here.
    """
    for env_key in ("PUBLIC_BASE_URL", "APP_BASE_URL"):
        raw = os.environ.get(env_key, "").strip()
        if raw:
            host = urlparse(raw).netloc or raw
            if host:
                return host
    if request is not None:
        netloc = request.url.netloc or request.headers.get("host", "")
        if netloc:
            return netloc
    return ""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _build_state_envelope(
    inner_state: str, tenant_host: str, callback_path: str, secret: str
) -> str:
    """Construct the 4-segment HMAC envelope the gateway expects.

    Format: ``<inner_state>.<b64host>.<b64path>.<b64sig>``
    where ``b64sig = base64url(hmac_sha256(secret, f"{inner}.{b64host}.{b64path}"))``.

    The HMAC body is the *literal three-segment string* (the b64-encoded
    forms, not the raw bytes) so a tampered host/path b64 ⇒ mismatched HMAC.
    Mirror of ``_parse_state_envelope`` in app-deployer's
    ``backend/api/auth_gateway.py`` — keep these two in lock-step.
    """
    if not inner_state or "." in inner_state:
        raise ValueError("inner_state must be non-empty and contain no '.'")
    if not tenant_host:
        raise ValueError("tenant_host is required to build a gateway envelope")
    if not callback_path.startswith("/"):
        raise ValueError("callback_path must start with '/'")
    if not secret:
        raise ValueError("HMAC secret is required to build a gateway envelope")
    b64host = _b64url_encode(tenant_host.encode("utf-8"))
    b64path = _b64url_encode(callback_path.encode("utf-8"))
    body = f"{inner_state}.{b64host}.{b64path}"
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _cookie_name() -> str:
    return os.environ.get("AUTH_COOKIE_NAME", "emly_admin_session")


def _refresh_cookie_name() -> str:
    """Cookie carrying the encrypted refresh_token. httpOnly + secure +
    samesite=lax; the JS surface never sees the value (the BFF's
    ``/api/auth/refresh`` is the only consumer)."""
    return os.environ.get("AUTH_REFRESH_COOKIE_NAME", "emly_admin_refresh")


def _expires_cookie_name() -> str:
    """Public cookie carrying the id_token's expiry as a Unix timestamp.

    ``httpOnly=False`` so the JS proactive-refresh interceptor can read
    it (the value is non-sensitive — it's the same expiry that's
    trivially recoverable by parsing the JWT, but the JWT itself is in
    the httpOnly sibling and the JS can't see that one).
    """
    return os.environ.get("AUTH_EXPIRES_COOKIE_NAME", "session_expires_at")


def _max_age_seconds(expires_at: datetime) -> int:
    """Cookie max-age aligned to the id_token's remaining lifetime.
    Clamped to a minimum of 60s to avoid a 0-max-age cookie that some
    browsers immediately discard."""
    delta = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return max(delta, 60)


def _set_session_cookies(response, request: Request, *, id_token: str, refresh_token: str | None, expires_at: datetime) -> None:
    """Set the trio of session cookies. Mirrors the inverse
    ``_clear_session_cookies`` so the browser attributes match."""
    max_age = _max_age_seconds(expires_at)
    response.set_cookie(
        key=_cookie_name(),
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
            key=_refresh_cookie_name(),
            value=encrypt_session_cookie(refresh_token) or "",
            max_age=max_age,
            path="/",
            secure=_cookie_secure(request),
            httponly=True,
            samesite=_cookie_samesite(),
            domain=_cookie_domain(),
        )
    response.set_cookie(
        key=_expires_cookie_name(),
        value=str(int(expires_at.timestamp())),
        max_age=max_age,
        path="/",
        secure=_cookie_secure(request),
        httponly=False,
        samesite=_cookie_samesite(),
        domain=_cookie_domain(),
    )


def _clear_session_cookies(response, request: Request) -> None:
    """Delete the trio of session cookies. Mirror attributes are required
    so the browser actually drops them (delete_cookie must match the
    path/domain/samesite used at set time)."""
    for name in (_cookie_name(), _refresh_cookie_name(), _expires_cookie_name()):
        response.delete_cookie(
            key=name,
            path="/",
            secure=_cookie_secure(request),
            samesite=_cookie_samesite(),
            domain=_cookie_domain(),
            httponly=(name != _expires_cookie_name()),
        )


def _cookie_secure(request: Request | None = None) -> bool:
    """Decide the ``Secure`` cookie attribute.

    The browser silently drops a ``Secure`` cookie when the response is
    served over plain HTTP, so the attribute must reflect the
    **actual** request scheme — not the configured ``APP_BASE_URL``.
    Otherwise a dev session whose ``APP_BASE_URL=https://example.com``
    points at production would fail to set cookies on ``http://localhost``,
    the catchall would see no session, and the user would get
    rate-limited bouncing between the catchall and ``/api/admin/auth/login``.

    Precedence:
    1. Explicit override via ``AUTH_COOKIE_SECURE=true|false`` (for
       deploys that front an HTTPS terminator while the app talks
       plain HTTP behind it).
    2. The request's actual ``url.scheme`` (HTTPS → True, HTTP → False).
       This is correct in every topology we care about:
       - Direct HTTPS: scheme is https.
       - HTTP localhost: scheme is http.
       - Reverse proxy (TLS terminated upstream): scheme is http
         but the cookie is set on the user's HTTPS browser, so the
         browser stores it just fine.
    3. Fallback when ``request`` is unavailable (module-init): default
       to ``False`` (the safe-ish choice — easier to debug a "cookie
       didn't set" than a "cookie silently dropped").
    """
    explicit = os.environ.get("AUTH_COOKIE_SECURE")
    if explicit is not None:
        return explicit.strip().lower() in ("1", "true", "yes", "on")
    if request is not None:
        return request.url.scheme.lower() == "https"
    return (os.environ.get("APP_BASE_URL", "").lower().startswith("https://"))


def _cookie_samesite() -> str:
    """Default to "lax" so the IdP's 302 → callback request still carries the
    cookie back. "strict" would block it on the very first redirect after
    setting and is what surfaces as "I logged in but bounced back to /login".
    """
    val = os.environ.get("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    return val if val in ("strict", "lax", "none") else "lax"


def _cookie_domain() -> str | None:
    val = os.environ.get("AUTH_COOKIE_DOMAIN", "").strip()
    return val or None


def _post_login_default(request: Request | None = None) -> str:
    return f"{_app_base_url(request)}/"


def _is_safe_return_to(target: str) -> bool:
    """Only allow same-origin returns to prevent open-redirect."""
    if not target:
        return False
    # Allow paths starting with /, but reject anything that could be a host.
    return target.startswith("/") and not target.startswith("//")


# -------- routes --------


@router.get("/auth/login", include_in_schema=False)
@limiter.limit(limit_for("auth_login"))
async def login(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
):
    return_to = request.query_params.get("return_to")
    if return_to is not None and not _is_safe_return_to(return_to):
        return_to = None

    oauth_state = OAuthState(
        state=generate_state(),
        code_verifier=generate_pkce_verifier(),
        nonce=generate_nonce(),
        return_to=return_to,
        created_at=datetime.now(timezone.utc),
    )
    get_state_store().put(oauth_state.state, oauth_state)

    # Gateway mode (centralized OIDC client across many tenant subdomains):
    # wrap the inner state in an HMAC envelope so the deployer's
    # ``/api/auth-gateway/callback`` can route the IdP's response back to
    # this tenant. State store stays keyed by the inner state — the
    # gateway strips the envelope wrapping before forwarding here, so the
    # callback handler doesn't change.
    secret = _gateway_hmac_secret()
    state_override: str | None = None
    if os.environ.get("AUTH_OIDC_REDIRECT_URI", "").strip() and secret:
        host = _tenant_host(request)
        if not host:
            log.error(
                "AUTH_OIDC_REDIRECT_URI is set but tenant host could not be resolved; "
                "skipping envelope wrap. Set PUBLIC_BASE_URL or APP_BASE_URL."
            )
        else:
            state_override = _build_state_envelope(
                oauth_state.state, host, _TENANT_CALLBACK_PATH, secret
            )

    url = provider.authorize_url(
        oauth_state, _redirect_uri(request), state_override=state_override
    )
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/callback", include_in_schema=False)
@limiter.limit(limit_for("auth_callback"))
async def callback(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
):
    state_token = request.query_params.get("state")
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        log.warning("OIDC callback returned error=%s description=%s", error, request.query_params.get("error_description"))
        audit("auth.idp_error", request=request, success=False, payload={"error": error})
        return JSONResponse(
            {"detail": {"code": "idp_error", "error": error}},
            status_code=400,
        )
    if not state_token or not code:
        audit("auth.callback_invalid", request=request, success=False, payload={"reason": "missing_state_or_code"})
        return JSONResponse(
            {"detail": {"code": "missing_state_or_code"}},
            status_code=400,
        )

    oauth_state = get_state_store().pop(state_token)
    if oauth_state is None:
        audit("auth.callback_state_invalid", request=request, success=False)
        return JSONResponse(
            {"detail": {"code": "callback_state_invalid_or_expired"}},
            status_code=400,
        )

    try:
        # exchange_code uses a sync httpx client. When AUTH_OIDC_ISSUER points
        # at the embedded issuer, the token request loops back to this same
        # process — running it inline would block the event loop and the
        # in-process /api/auth/local/token handler would deadlock until httpx
        # times out. Hand it to the threadpool so the loop stays responsive.
        token_set, principal = await run_in_threadpool(
            provider.exchange_code, code, oauth_state, _redirect_uri(request)
        )
    except InvalidTokenError as e:
        log.warning("OIDC code exchange failed: %s", e)
        audit("auth.code_exchange_failed", request=request, success=False, payload={"err": str(e)})
        return JSONResponse(
            {"detail": {"code": "code_exchange_failed", "message": str(e)}},
            status_code=400,
        )
    except AuthError as e:
        audit("auth.idp_error", request=request, success=False, payload={"err": str(e)})
        return JSONResponse({"detail": {"code": "idp_error", "message": str(e)}}, status_code=400)

    # Resolve / create the admin row. AdminNotProvisioned etc. are mapped here
    # to a redirect at the UI; we render a plain JSON body and let the UI
    # decide how to display it (the / static catch-all serves /request-access).
    from services.auth.user_resolver import (
        AdminNotProvisioned,
        EmailLinkedToOtherProvider,
        EmailNotVerified,
        resolve_or_create_admin,
    )

    try:
        admin = resolve_or_create_admin(principal)
    except AdminNotProvisioned:
        audit("auth.admin_not_provisioned", request=request, success=False,
              payload={"email": principal.email, "issuer": principal.issuer})
        return RedirectResponse(
            f"{_app_base_url(request)}/request-access?email={principal.email}",
            status_code=status.HTTP_302_FOUND,
        )
    except EmailLinkedToOtherProvider:
        audit("auth.relink_refused", request=request, success=False,
              payload={"email": principal.email, "issuer": principal.issuer})
        return JSONResponse(
            {"detail": {"code": "email_linked_to_other_provider"}},
            status_code=401,
        )
    except EmailNotVerified:
        audit("auth.email_not_verified", request=request, success=False,
              payload={"email": principal.email, "issuer": principal.issuer})
        return JSONResponse(
            {"detail": {"code": "email_not_verified", "email": principal.email}},
            status_code=401,
        )

    if not admin.is_active:
        audit("auth.account_disabled", admin_id=admin.id, request=request, success=False)
        return JSONResponse(
            {"detail": {"code": "account_disabled"}},
            status_code=403,
        )

    target = oauth_state.return_to or _post_login_default(request)
    # If this admin has any new-style space invites still pending, send
    # them to the picker so they can accept/reject explicitly — unless
    # they came in via a token-bearing link, in which case ``return_to``
    # already points at ``/accept-invite?token=...`` and we honour it.
    try:
        from models.pending_admins import KIND_SPACE_INVITE, PendingAdmins

        space_invites = PendingAdmins.list_pending_for_email(
            admin.email, kind=KIND_SPACE_INVITE
        )
    except Exception:
        space_invites = []
    if space_invites:
        token_in_return = (
            oauth_state.return_to is not None
            and "/accept-invite" in oauth_state.return_to
        )
        if not token_in_return:
            target = "/accept-invite"
    # Always normalise to absolute (return_to may be a path-only string).
    if target.startswith("/"):
        target = f"{_app_base_url(request)}{target}"

    response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    max_age = _max_age_seconds(token_set.expires_at)
    # Persist the id_token, not the access_token. The id_token is the
    # OIDC artifact that carries identity claims (sub/email/nonce); some
    # IdPs return opaque access_tokens with the wrong audience that
    # would fail subsequent verify_token() calls.
    session_token = token_set.id_token or token_set.access_token
    _set_session_cookies(
        response, request,
        id_token=session_token,
        refresh_token=token_set.refresh_token,
        expires_at=token_set.expires_at,
    )
    log.info(
        "Admin %s signed in via %s (max_age=%ds, refresh_token=%s)",
        admin.id, principal.issuer, max_age,
        "yes" if token_set.refresh_token else "no",
    )
    audit("auth.login", admin_id=admin.id, request=request, success=True,
          payload={"issuer": principal.issuer, "has_refresh_token": bool(token_set.refresh_token)})
    return response


@router.post("/auth/logout", include_in_schema=False)
async def logout(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
):
    """Clear all session cookies and revoke the refresh_token at the IdP.

    Returns the IdP's end-session URL (if any) so the UI can redirect
    there for global logout. Note: the access_token itself remains
    valid until ``exp`` for any party that captured it — full
    revocation is a Phase 11 future-item (Redis-backed jti
    blacklisting). With the BFF refresh tokens now in play, the
    worst-case replay window is the access-token TTL (default 1h
    for the embedded issuer) and the lost-the-cookie path triggers
    a hard redirect-to-IdP on the next API call anyway.
    """
    response = JSONResponse(
        {"provider_logout_url": provider.logout_url(_app_base_url(request))}
    )
    _clear_session_cookies(response, request)

    # If the BFF still has the refresh_token in memory, hand it to the
    # embedded issuer so the store-side entry is dropped. External IdPs
    # (Auth0, Clerk, etc.) don't expose this surface in our Protocol —
    # they treat logout as "stop using it" and the cookie clear is
    # what blocks replay from this browser.
    try:
        from services.secrets import decrypt_session_cookie
        from services.auth.issuer.factory import is_local_issuer_enabled
        if is_local_issuer_enabled():
            raw = request.cookies.get(_refresh_cookie_name())
            if raw:
                rt = decrypt_session_cookie(raw)
                if rt:
                    # Fire-and-forget: the issuer's /logout endpoint
                    # is the only path that takes a refresh_token.
                    # The store revoke is best-effort — if the IdP
                    # is unreachable the cookie clear is still
                    # what protects the user.
                    import httpx
                    try:
                        httpx.post(
                            f"{_app_base_url(request)}/api/auth/local/logout",
                            data={"refresh_token": rt},
                            timeout=2.0,
                        )
                    except Exception:
                        log.debug("BFF logout: embedded-issuer revoke failed (non-fatal)")
    except Exception:
        log.debug("BFF logout: refresh-token cleanup skipped", exc_info=True)

    audit("auth.logout", request=request, success=True)
    return response


@router.get("/auth/me", include_in_schema=False)
async def me(admin = Depends(get_admin)):
    """Returns the resolved admin row + memberships. The UI calls this on app
    boot to know who's signed in and what they can access."""
    from models.admin_bot_memberships import AdminBotMemberships
    from services.authz import can_create_bots

    memberships = AdminBotMemberships.list_for_admin(admin.id)
    return {
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
            {"bot_id": m.bot_id, "role": m.role}
            for m in memberships
        ],
        # Lets the UI hide "Create bot" when BOT_CREATION doesn't allow it.
        "can_create_bots": can_create_bots(admin),
    }
