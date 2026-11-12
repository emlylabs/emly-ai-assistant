"""Singleton accessor for the configured ``AuthProvider`` and ``StateStore``.

Routes use ``Depends(get_auth_provider)``. The choice of issuer is encoded
entirely in environment variables; this module is the seam between env config
and the rest of the app.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from services.auth.base import AuthProvider, UnknownProviderError
from services.auth.oidc import OidcProvider
from services.auth.state_store import InMemoryStateStore, StateStore

log = logging.getLogger(__name__)

_lock = threading.Lock()
_provider: Optional[AuthProvider] = None
_state_store: Optional[StateStore] = None


def _bool_env(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise UnknownProviderError(f"required env var {name} is not set")
    return v


def _build_provider() -> AuthProvider:
    # When the embedded issuer is the default, audience and client_id mirror
    # its defaults so the app boots out-of-the-box with just
    # AUTH_BOOTSTRAP_SUPERADMIN_EMAIL set. Operators pointing at an external
    # IdP (Auth0, Clerk, Cognito, …) supply these explicitly via env.
    from services.auth.issuer.factory import (
        configured_audience as _embedded_audience,
        configured_client_id as _embedded_client_id,
        issuer_url as _embedded_issuer_url,
    )

    explicit_issuer = os.environ.get("AUTH_OIDC_ISSUER")
    embedded_issuer = _embedded_issuer_url()
    issuer = explicit_issuer or embedded_issuer
    audience = os.environ.get("AUTH_OIDC_AUDIENCE") or _embedded_audience()
    client_id = os.environ.get("AUTH_OIDC_CLIENT_ID") or _embedded_client_id()
    client_secret = os.environ.get("AUTH_OIDC_CLIENT_SECRET") or None
    scopes = os.environ.get("AUTH_OIDC_SCOPES", "openid email profile")
    jwks_ttl = int(os.environ.get("AUTH_OIDC_JWKS_CACHE_TTL", "3600"))
    leeway = int(os.environ.get("AUTH_OIDC_LEEWAY_SECONDS", "30"))
    aud_fallback = _bool_env("AUTH_OIDC_AUDIENCE_FALLBACK_TO_CLIENT_ID", False)
    # "Embedded" means the issuer is THIS app's own embedded OIDC issuer.
    # Detection is robust against operators pinning ``AUTH_OIDC_ISSUER``
    # to an *external* IdP (Keycloak, Auth0, …) — that's the case where
    # the prior heuristic, which compared against ``_embedded_issuer_url()``
    # that itself reads ``AUTH_OIDC_ISSUER``, would falsely say "embedded"
    # and bake in the hardcoded ``/api/auth/local/*`` endpoint paths.
    # Two ways to be embedded:
    #   - no ``AUTH_OIDC_ISSUER`` set → defaulting to embedded by definition
    #   - it's explicitly set to the app's own base URL (operator wants
    #     the embedded issuer with a custom ``iss`` claim).
    app_base = (os.environ.get("APP_BASE_URL") or "").rstrip("/")
    if explicit_issuer:
        is_embedded = bool(app_base) and explicit_issuer.rstrip("/") == app_base
    else:
        is_embedded = True
    # Trust the IdP's ``email_verified`` claim for external issuers
    # (where we don't own the password gate). For the embedded issuer
    # we always trust the gate (a successful password verify proves
    # control of the address).
    trust_email_unconditionally = is_embedded or _bool_env(
        "AUTH_OIDC_TRUST_EMAIL_UNCONDITIONALLY", False
    )

    log.info(
        "OIDC: configuring provider issuer=%s audience=%s client_id=%s aud_fallback=%s embedded=%s",
        issuer, audience, client_id, aud_fallback, is_embedded,
    )

    discovery_override = None
    jwks_provider = None
    token_exchanger = None
    if is_embedded:
        # Self-loopback over the external hostname won't resolve from
        # inside the pod, so build the discovery doc + JWKS in-process
        # and bypass the token-endpoint HTTP round-trip too. Mirrors
        # the live endpoints in ``routes/auth_issuer.py``.
        base = issuer.rstrip("/")
        discovery_override = {
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
        from services.auth.issuer.factory import (
            access_token_ttl_seconds,
            configured_audience,
            configured_client_id,
            get_code_store,
            get_keystore,
            get_refresh_store,
        )
        jwks_provider = lambda: get_keystore().jwks_dict()

        def _local_token_exchange(data: dict) -> dict:
            from services.auth.base import InvalidTokenError
            from services.auth.issuer.factory import refresh_token_ttl_seconds
            from services.auth.issuer.flows import (
                CodeExchangeError,
                consume_code,
                consume_refresh_token,
                issue_refresh_token,
            )
            from services.auth.issuer.tokens import mint_access_token, mint_id_token

            if data.get("client_id") != configured_client_id():
                raise InvalidTokenError("unknown client_id")
            grant_type = data.get("grant_type")
            if grant_type not in ("authorization_code", "refresh_token"):
                raise InvalidTokenError(
                    f"unsupported grant_type: {grant_type!r}"
                )

            keystore = get_keystore()
            ttl = access_token_ttl_seconds()
            audience = configured_audience()
            issuer = base

            if grant_type == "authorization_code":
                try:
                    record = consume_code(
                        code_store=get_code_store(),
                        code=data["code"],
                        code_verifier=data["code_verifier"],
                        redirect_uri=data["redirect_uri"],
                    )
                except CodeExchangeError as e:
                    raise InvalidTokenError(str(e)) from e

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
                body: dict = {
                    "access_token": access,
                    "id_token": id_token,
                    "token_type": "Bearer",
                    "expires_in": ttl,
                    "scope": record.scope,
                }
                if "offline_access" in (record.scope or "").split():
                    from datetime import timedelta
                    body["refresh_token"] = issue_refresh_token(
                        refresh_store=get_refresh_store(),
                        admin_id=record.admin_id,
                        email=record.email,
                        email_verified=record.email_verified,
                        name=record.name,
                        scope=record.scope,
                        ttl=timedelta(seconds=refresh_token_ttl_seconds()),
                    )
                return body

            # grant_type == "refresh_token"
            try:
                record = consume_refresh_token(
                    refresh_store=get_refresh_store(),
                    refresh_token=data.get("refresh_token", ""),
                )
            except CodeExchangeError as e:
                raise InvalidTokenError(str(e)) from e

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
                nonce=None,
                ttl_seconds=ttl,
                name=record.name,
            )
            from datetime import timedelta
            new_refresh_token = issue_refresh_token(
                refresh_store=get_refresh_store(),
                admin_id=record.admin_id,
                email=record.email,
                email_verified=record.email_verified,
                name=record.name,
                scope=record.scope,
                ttl=timedelta(seconds=refresh_token_ttl_seconds()),
            )
            return {
                "access_token": access,
                "id_token": id_token,
                "token_type": "Bearer",
                "expires_in": ttl,
                "scope": record.scope,
                "refresh_token": new_refresh_token,
            }

        token_exchanger = _local_token_exchange

    return OidcProvider(
        issuer=issuer,
        audience=audience,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
        jwks_cache_ttl=jwks_ttl,
        leeway_seconds=leeway,
        audience_fallback_to_client_id=aud_fallback,
        trust_email_unconditionally=trust_email_unconditionally,
        discovery_override=discovery_override,
        jwks_provider=jwks_provider,
        token_exchanger=token_exchanger,
    )


def get_auth_provider() -> AuthProvider:
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                _provider = _build_provider()
    return _provider


def get_state_store() -> StateStore:
    global _state_store
    if _state_store is None:
        with _lock:
            if _state_store is None:
                _state_store = InMemoryStateStore()
    return _state_store


def reset_for_tests() -> None:
    """Clear singletons so the next ``get_*()`` rebuilds. Tests only."""
    global _provider, _state_store
    with _lock:
        _provider = None
        _state_store = None
