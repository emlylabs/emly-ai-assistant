"""Embedded OIDC issuer — FastAPI routes.

Mounted only when ``AUTH_LOCAL_ISSUER_ENABLED=true`` (the default). The shape
matches a standard OIDC IdP so ``services/auth/oidc.py`` can verify tokens
through the same code path it uses for any external IdP.

Endpoints:

- ``GET /.well-known/openid-configuration`` — discovery doc.
- ``GET /.well-known/jwks.json`` — public JWKS from the keystore.
- ``GET  /api/auth/local/authorize`` — minimal HTML login form.
- ``POST /api/auth/local/authorize`` — verify creds, mint code, redirect.
- ``POST /api/auth/local/token`` — exchange code for access + id tokens;
  also accepts ``grant_type=refresh_token`` for BFF silent renewal.
- ``POST /api/auth/local/logout`` — issuer-side logout. Accepts an
  optional ``refresh_token`` form field to revoke the BFF token.

Password-reset, email-verify, and forced-password-change endpoints are
deferred to Phase 2c (still in scope for Phase 2 but separately committed).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from models.admin_users import AdminUsers
from models.local_credentials import LocalCredentials
from services.auth.issuer.factory import (
    access_token_ttl_seconds,
    allowed_redirect_uris,
    configured_audience,
    configured_client_id,
    get_code_store,
    get_keystore,
    get_refresh_store,
    issuer_url,
    lockout_duration_seconds,
    lockout_threshold,
    refresh_token_ttl_seconds,
)
from services.auth.issuer.flows import (
    CodeExchangeError,
    FlowError,
    InvalidAuthorizeRequest,
    InvalidRedirectUriError,
    UnknownClientError,
    check_client,
    consume_code,
    consume_refresh_token,
    issue_code,
    issue_refresh_token,
    parse_authorize_request,
)
from services.auth.issuer.passwords import hash_password, needs_rehash, verify_password
from services.auth.ratelimit import rate_limit
from services.auth.issuer.tokens import mint_access_token, mint_id_token

log = logging.getLogger(__name__)

router = APIRouter()


# -------- discovery --------


@router.get("/.well-known/openid-configuration", include_in_schema=False)
async def openid_configuration(request: Request) -> JSONResponse:
    base = issuer_url(request)
    doc = {
        "issuer": base,
        "authorization_endpoint": f"{base}/api/auth/local/authorize",
        "token_endpoint": f"{base}/api/auth/local/token",
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "end_session_endpoint": f"{base}/api/auth/local/logout",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile", "offline_access"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
    }
    return JSONResponse(doc)


@router.get("/.well-known/jwks.json", include_in_schema=False)
async def jwks() -> JSONResponse:
    return JSONResponse(get_keystore().jwks_dict())


# -------- authorize --------


# BotForge token sheet — see ui/app/globals.css for the canonical version.
# Inlined here because this page is served outside the React app shell.
# The doubled curly braces are escaped for the str.format(...) call below.
_AUTHORIZE_FORM_HTML = """<!doctype html>
<html lang="en" data-theme="ink" data-density="comfortable">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in &middot; Emly</title>
  <style>
    :root {{
      --bg:      oklch(97% 0.012 80);
      --surface: oklch(99% 0.005 80);
      --fg:      oklch(20% 0.02 60);
      --muted:   oklch(48% 0.015 60);
      --border:  oklch(89% 0.012 80);
      --accent:  oklch(58% 0.16 35);

      --font-display: "Iowan Old Style", Charter, Georgia, serif;
      --font-body:    -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      --font-mono:    ui-monospace, "JetBrains Mono", "SF Mono", Menlo, monospace;

      --accent-soft: color-mix(in oklch, var(--accent) 13%, transparent);
      --fg-soft:     color-mix(in oklch, var(--fg) 5%, transparent);
      --paper:       color-mix(in oklch, var(--surface) 84%, var(--bg));

      --error:      oklch(58% 0.18 28);
      --error-soft: color-mix(in oklch, var(--error) 14%, transparent);

      --radius: 8px;
    }}
    [data-theme="ink"] {{
      --bg:      oklch(18% 0.02 60);
      --surface: oklch(23% 0.018 60);
      --fg:      oklch(96% 0.008 80);
      --muted:   oklch(73% 0.015 70);
      --border:  oklch(34% 0.018 60);
      --paper:   oklch(15% 0.018 60);
    }}

    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font-family: var(--font-body);
      font-size: 14px;
      line-height: 1.55;
      -webkit-font-smoothing: antialiased;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}

    h1 {{
      margin: 0;
      font-family: var(--font-display);
      font-size: 28px;
      font-weight: 600;
      letter-spacing: -0.018em;
      line-height: 1.15;
    }}

    .eyebrow {{
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .eyebrow::before {{
      content: "";
      display: inline-block;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--accent);
      margin-right: 8px;
      vertical-align: 1px;
    }}

    .card {{
      width: 100%;
      max-width: 380px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 28px;
    }}

    label {{
      display: block;
      margin-top: 16px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 6px;
    }}

    input[type="email"], input[type="password"] {{
      width: 100%;
      min-height: 38px;
      padding: 8px 12px;
      background: color-mix(in oklch, var(--surface) 72%, var(--bg));
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--fg);
      font: inherit;
      transition: border-color .15s, box-shadow .15s;
    }}
    input:focus {{
      outline: none;
      border-color: var(--fg);
      box-shadow: 0 0 0 3px var(--fg-soft);
    }}

    button {{
      display: block;
      width: 100%;
      margin-top: 24px;
      min-height: 40px;
      padding: 10px 16px;
      background: var(--fg);
      color: var(--surface);
      border: 1px solid var(--fg);
      border-radius: var(--radius);
      font-family: inherit;
      font-size: 14px;
      font-weight: 600;
      letter-spacing: -0.005em;
      cursor: pointer;
      transition: background .16s, transform .08s;
    }}
    button:hover {{ background: color-mix(in oklch, var(--fg) 88%, var(--surface)); }}
    button:active {{ transform: translateY(1px); }}

    .err {{
      margin-top: 16px;
      padding: 10px 12px;
      background: var(--error-soft);
      border: 1px solid color-mix(in oklch, var(--error) 40%, transparent);
      border-radius: 6px;
      color: var(--fg);
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <main class="card">
    <p class="eyebrow">Emly admin</p>
    <h1 style="margin-top: 8px;">Sign in</h1>
    <form method="post" action="/api/auth/local/authorize">
      <input type="hidden" name="client_id" value="{client_id}">
      <input type="hidden" name="redirect_uri" value="{redirect_uri}">
      <input type="hidden" name="state" value="{state}">
      <input type="hidden" name="nonce" value="{nonce}">
      <input type="hidden" name="code_challenge" value="{code_challenge}">
      <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
      <input type="hidden" name="scope" value="{scope}">
      <label for="email">Email</label>
      <input id="email" name="email" type="email" required autofocus autocomplete="username">
      <label for="password">Password</label>
      <input id="password" name="password" type="password" required autocomplete="current-password">
      <button type="submit">Sign in</button>
      {error_html}
    </form>
  </main>
</body>
</html>
"""


def _render_authorize_form(*, params: dict, error: str | None = None) -> HTMLResponse:
    """Render the local-issuer login form.

    All interpolated values are HTML-escaped (with quote=True so they're safe
    in attribute context). Format-validation in ``parse_authorize_request``
    already rejects malformed state/nonce/code_challenge before we reach this
    function — escaping here is the second-layer defense for values that pass
    format validation but contain attacker bytes (notably ``redirect_uri`` and
    ``scope``, where the allowlist lives in env config, not in the parser).
    """
    html = _AUTHORIZE_FORM_HTML.format(
        client_id=html_escape(params.get("client_id", ""), quote=True),
        redirect_uri=html_escape(params.get("redirect_uri", ""), quote=True),
        state=html_escape(params.get("state", ""), quote=True),
        nonce=html_escape(params.get("nonce") or "", quote=True),
        code_challenge=html_escape(params.get("code_challenge", ""), quote=True),
        code_challenge_method=html_escape(
            params.get("code_challenge_method", "S256"), quote=True
        ),
        scope=html_escape(params.get("scope", "openid email profile"), quote=True),
        error_html=(
            f'<p class="err">{html_escape(error, quote=True)}</p>' if error else ""
        ),
    )
    return HTMLResponse(html)


def _4xx(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


@router.get(
    "/api/auth/local/authorize",
    include_in_schema=False,
    dependencies=[Depends(rate_limit("local_authorize"))],
)
async def authorize_get(request: Request):
    """Render the login form. Validates query params first so a malformed link 400s early."""
    params = dict(request.query_params)
    try:
        authz = parse_authorize_request(
            client_id=params.get("client_id"),
            redirect_uri=params.get("redirect_uri"),
            state=params.get("state"),
            nonce=params.get("nonce"),
            code_challenge=params.get("code_challenge"),
            code_challenge_method=params.get("code_challenge_method"),
            response_type=params.get("response_type"),
            scope=params.get("scope"),
        )
        check_client(
            authz,
            configured_client_id=configured_client_id(),
            allowed_redirect_uris=allowed_redirect_uris(request),
        )
    except (InvalidAuthorizeRequest, UnknownClientError, InvalidRedirectUriError) as e:
        return _4xx(str(e))
    return _render_authorize_form(params=params)


@router.post(
    "/api/auth/local/authorize",
    include_in_schema=False,
    dependencies=[Depends(rate_limit("local_authorize"))],
)
async def authorize_post(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(...),
    code_challenge: str = Form(...),
    code_challenge_method: str = Form("S256"),
    nonce: str | None = Form(None),
    scope: str = Form("openid email profile"),
    email: str = Form(...),
    password: str = Form(...),
):
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
    }
    try:
        authz = parse_authorize_request(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            response_type="code",
            scope=scope,
        )
        check_client(
            authz,
            configured_client_id=configured_client_id(),
            allowed_redirect_uris=allowed_redirect_uris(request),
        )
    except FlowError as e:
        return _4xx(str(e))

    # -------- credential check --------
    #
    # The JWT ``sub`` is always the lowercased email — stable across the
    # pre-activation and post-activation states so ``user_resolver`` matches
    # by ``(issuer, subject)`` consistently regardless of whether the
    # ``admin_user`` row has been created yet.
    #
    # The CREDENTIAL key (where we look up the password) does change:
    #   (a) pre-activation — keyed on ``bootstrap:<email>`` (seeded by main.py).
    #   (b) activated      — keyed on ``admin.id`` (migrated by user_resolver
    #                        the first time the activation flow runs).
    sub_for_token = email.lower()
    admin = AdminUsers.get_by_email(email)
    if admin is not None:
        credential_key = admin.id
        token_email = admin.email
        token_name = admin.name
        token_email_verified = True  # embedded issuer trusts the password gate
    else:
        from models.pending_admins import PendingAdmins
        pending = PendingAdmins.get_active_by_email(email)
        if pending is None:
            return _render_authorize_form(params=params, error="Invalid email or password")
        credential_key = f"bootstrap:{email.lower()}"
        token_email = pending.email
        token_name = None
        token_email_verified = True

    if LocalCredentials.is_locked(credential_key):
        return _render_authorize_form(
            params=params,
            error="Account is temporarily locked due to too many failed attempts. Try again later.",
        )

    pw_hash = LocalCredentials.get_password_hash(credential_key)
    if pw_hash is None or not verify_password(password, pw_hash):
        LocalCredentials.record_failure(
            credential_key,
            lockout_threshold=lockout_threshold(),
            lockout_duration_seconds=lockout_duration_seconds(),
        )
        return _render_authorize_form(params=params, error="Invalid email or password")

    # Opportunistic Argon2 parameter upgrade. If the stored hash uses
    # outdated cost parameters, transparently rehash on this successful
    # login so the next attempt verifies against the bumped values.
    if needs_rehash(pw_hash):
        try:
            LocalCredentials.rehash_password(credential_key, hash_password(password))
        except Exception:
            log.exception("opportunistic rehash failed for %s", credential_key)

    # Optional ``must_change`` enforcement. By design ``must_change=True``
    # is set on the bootstrap-seeded credential to signal "rotate me on
    # first login." The rotate-password endpoint is Phase 2c future work,
    # so out of the box this flag is informational — flipping it to a
    # hard gate would lock out any deployment whose admin was bootstrapped
    # before a rotate-flow exists. Operators with an out-of-band rotate
    # path can opt in via ``AUTH_LOCAL_ENFORCE_MUST_CHANGE=true``; the
    # bootstrap sentinel itself is always exempt so the very first login
    # can complete and migrate to a real admin row.
    if (
        os.environ.get("AUTH_LOCAL_ENFORCE_MUST_CHANGE", "").strip().lower()
        in ("1", "true", "yes", "on")
        and not credential_key.startswith("bootstrap:")
    ):
        cred = LocalCredentials.get(credential_key)
        if cred is not None and cred.must_change:
            return _render_authorize_form(
                params=params,
                error=(
                    "This account must change its password before it can sign in. "
                    "Contact your administrator."
                ),
            )

    LocalCredentials.record_success(credential_key)

    code = issue_code(
        code_store=get_code_store(),
        authz=authz,
        admin_id=sub_for_token,
        email=token_email,
        email_verified=token_email_verified,
        name=token_name,
    )
    redirect_url = f"{redirect_uri}?{urlencode({'code': code, 'state': state})}"
    return RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)


# -------- token --------


def _mint_token_set(
    *,
    keystore,
    issuer: str,
    audience: str,
    admin_id: str,
    email: str,
    email_verified: bool,
    name: str | None,
    nonce: str | None,
    ttl: int,
) -> tuple[str, str]:
    """Mint access + id tokens for the given identity. Centralised so the
    initial ``authorization_code`` exchange and the ``refresh_token`` grant
    produce identical claims.
    """
    access = mint_access_token(
        keystore=keystore,
        issuer=issuer,
        audience=audience,
        subject=admin_id,
        email=email,
        email_verified=email_verified,
        ttl_seconds=ttl,
    )
    id_token = mint_id_token(
        keystore=keystore,
        issuer=issuer,
        audience=audience,
        subject=admin_id,
        email=email,
        email_verified=email_verified,
        nonce=nonce,
        ttl_seconds=ttl,
        name=name,
    )
    return access, id_token


@router.post(
    "/api/auth/local/token",
    include_in_schema=False,
    dependencies=[Depends(rate_limit("local_token"))],
)
async def token(
    request: Request,
    grant_type: str = Form(...),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    code_verifier: str | None = Form(None),
    refresh_token: str | None = Form(None),
    client_id: str = Form(...),
    client_secret: str | None = Form(None),
    scope: str | None = Form(None),
):
    if client_id != configured_client_id():
        return _4xx("unknown client_id")

    keystore = get_keystore()
    issuer = issuer_url(None)
    audience = configured_audience()
    ttl = access_token_ttl_seconds()

    if grant_type == "authorization_code":
        if not code or not code_verifier or not redirect_uri:
            return _4xx("missing code, code_verifier, or redirect_uri")
        try:
            record = consume_code(
                code_store=get_code_store(),
                code=code,
                code_verifier=code_verifier,
                redirect_uri=redirect_uri,
            )
        except CodeExchangeError as e:
            return _4xx(str(e))

        access, id_token = _mint_token_set(
            keystore=keystore,
            issuer=issuer,
            audience=audience,
            admin_id=record.admin_id,
            email=record.email,
            email_verified=record.email_verified,
            name=record.name,
            nonce=record.nonce,
            ttl=ttl,
        )
        body: dict = {
            "access_token": access,
            "id_token": id_token,
            "token_type": "Bearer",
            "expires_in": ttl,
            "scope": record.scope,
        }
        # Mint a refresh_token only when the granted scope included
        # ``offline_access`` (OIDC §11). The BFF stores this opaque
        # token in the encrypted refresh cookie for silent renewal.
        if "offline_access" in (record.scope or "").split():
            body["refresh_token"] = issue_refresh_token(
                refresh_store=get_refresh_store(),
                admin_id=record.admin_id,
                email=record.email,
                email_verified=record.email_verified,
                name=record.name,
                scope=record.scope,
                ttl=timedelta(seconds=refresh_token_ttl_seconds()),
            )
        return JSONResponse(body)

    if grant_type == "refresh_token":
        if not refresh_token:
            return _4xx("missing refresh_token")
        try:
            record = consume_refresh_token(
                refresh_store=get_refresh_store(),
                refresh_token=refresh_token,
            )
        except CodeExchangeError as e:
            return _4xx(str(e))

        # Single-use semantics: the consumed token is invalidated by
        # ``pop`` above. The response carries a freshly-minted
        # refresh_token so the BFF can roll the cookie forward without
        # losing the silent-renewal chain.
        access, id_token = _mint_token_set(
            keystore=keystore,
            issuer=issuer,
            audience=audience,
            admin_id=record.admin_id,
            email=record.email,
            email_verified=record.email_verified,
            name=record.name,
            nonce=None,
            ttl=ttl,
        )
        new_refresh_token = issue_refresh_token(
            refresh_store=get_refresh_store(),
            admin_id=record.admin_id,
            email=record.email,
            email_verified=record.email_verified,
            name=record.name,
            scope=record.scope,
            ttl=timedelta(seconds=refresh_token_ttl_seconds()),
        )
        return JSONResponse(
            {
                "access_token": access,
                "id_token": id_token,
                "token_type": "Bearer",
                "expires_in": ttl,
                "scope": record.scope,
                "refresh_token": new_refresh_token,
            }
        )

    return _4xx(f"unsupported grant_type: {grant_type!r}")


# -------- logout --------


@router.post("/api/auth/local/logout", include_in_schema=False)
async def logout(refresh_token: str | None = Form(None)) -> JSONResponse:
    """Issuer-side logout.

    For Phase 11's stateless access tokens this is a no-op; the BFF
    clears the session cookies on its end. If a ``refresh_token`` is
    provided (BFF logout passes it back so we can revoke at the IdP),
    we drop it from the refresh store so a stolen-token replay after
    logout fails.
    """
    if refresh_token:
        get_refresh_store().revoke(refresh_token)
    return JSONResponse({"ok": True})


# (the second `logout` route was the original; it has been replaced
#  by the refresh-token-aware version above)
