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

    issuer = os.environ.get("AUTH_OIDC_ISSUER") or _embedded_issuer_url()
    audience = os.environ.get("AUTH_OIDC_AUDIENCE") or _embedded_audience()
    client_id = os.environ.get("AUTH_OIDC_CLIENT_ID") or _embedded_client_id()
    client_secret = os.environ.get("AUTH_OIDC_CLIENT_SECRET") or None
    scopes = os.environ.get("AUTH_OIDC_SCOPES", "openid email profile")
    jwks_ttl = int(os.environ.get("AUTH_OIDC_JWKS_CACHE_TTL", "3600"))
    leeway = int(os.environ.get("AUTH_OIDC_LEEWAY_SECONDS", "30"))
    aud_fallback = _bool_env("AUTH_OIDC_AUDIENCE_FALLBACK_TO_CLIENT_ID", False)

    log.info(
        "OIDC: configuring provider issuer=%s audience=%s client_id=%s aud_fallback=%s",
        issuer, audience, client_id, aud_fallback,
    )
    return OidcProvider(
        issuer=issuer,
        audience=audience,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
        jwks_cache_ttl=jwks_ttl,
        leeway_seconds=leeway,
        audience_fallback_to_client_id=aud_fallback,
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
