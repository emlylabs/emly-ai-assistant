"""Embedded OIDC issuer — FastAPI routes.

Mounted only when ``AUTH_LOCAL_ISSUER_ENABLED=true`` (the default). The shape
matches a standard OIDC IdP so ``services/auth/oidc.py`` can verify tokens
through the same code path it uses for any external IdP.

Endpoints:

- ``GET /.well-known/openid-configuration`` — discovery doc.
- ``GET /.well-known/jwks.json`` — public JWKS from the keystore.
- ``GET  /api/auth/local/authorize`` — minimal HTML login form.
- ``POST /api/auth/local/authorize`` — verify creds, mint code, redirect.
- ``POST /api/auth/local/token`` — exchange code for access + id tokens.
- ``POST /api/auth/local/logout`` — issuer-side logout (no-op for stateless tokens).

Password-reset, email-verify, and forced-password-change endpoints are
deferred to Phase 2c (still in scope for Phase 2 but separately committed).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape as html_escape
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request, status
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
    issuer_url,
    lockout_duration_seconds,
    lockout_threshold,
)
from services.auth.issuer.flows import (
    CodeExchangeError,
    FlowError,
    InvalidAuthorizeRequest,
    InvalidRedirectUriError,
    UnknownClientError,
    check_client,
    consume_code,
    issue_code,
    parse_authorize_request,
)
from services.auth.issuer.passwords import verify_password
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
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile"],
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


@router.get("/api/auth/local/authorize", include_in_schema=False)
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


@router.post("/api/auth/local/authorize", include_in_schema=False)
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


@router.post("/api/auth/local/token", include_in_schema=False)
async def token(
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    code_verifier: str = Form(...),
    client_id: str = Form(...),
    client_secret: str | None = Form(None),
):
    if grant_type != "authorization_code":
        return _4xx(f"unsupported grant_type: {grant_type!r}")
    if client_id != configured_client_id():
        return _4xx("unknown client_id")

    try:
        record = consume_code(
            code_store=get_code_store(),
            code=code,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
        )
    except CodeExchangeError as e:
        return _4xx(str(e))

    keystore = get_keystore()
    # The token endpoint is hit by the verifier's loopback httpx client, so
    # request-derived URLs would all be ``http://127.0.0.1:8080`` anyway. We
    # use the env/localhost value explicitly so the ``iss`` claim matches the
    # verifier's expected issuer regardless of how exchange_code reached us.
    issuer = issuer_url(None)
    audience = configured_audience()
    ttl = access_token_ttl_seconds()

    access = mint_access_token(
        keystore=keystore,
        issuer=issuer,
        audience=audience,
        subject=record.admin_id,
        email=record.email,
        email_verified=record.email_verified,
        ttl_seconds=ttl,
    )
    id_token = mint_id_token(
        keystore=keystore,
        issuer=issuer,
        audience=audience,
        subject=record.admin_id,
        email=record.email,
        email_verified=record.email_verified,
        nonce=record.nonce,
        ttl_seconds=ttl,
        name=record.name,
    )
    return JSONResponse(
        {
            "access_token": access,
            "id_token": id_token,
            "token_type": "Bearer",
            "expires_in": ttl,
            "scope": record.scope,
        }
    )


# -------- logout --------


@router.post("/api/auth/local/logout", include_in_schema=False)
async def logout() -> JSONResponse:
    """Issuer-side logout. Tokens are stateless until Phase 12 adds revocation."""
    return JSONResponse({"ok": True})
