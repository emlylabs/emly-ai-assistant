"""Singleton accessors for the embedded issuer's stateful pieces.

The keystore and the code store are both process-local singletons. Tests can
swap them via ``reset_for_tests``.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from services.auth.issuer.flows import CodeStore, InMemoryCodeStore
from services.auth.issuer.keystore import Keystore

log = logging.getLogger(__name__)

_lock = threading.Lock()
_keystore: Optional[Keystore] = None
_code_store: Optional[CodeStore] = None


def is_local_issuer_enabled() -> bool:
    val = os.environ.get("AUTH_LOCAL_ISSUER_ENABLED", "true").strip().lower()
    return val in ("1", "true", "yes", "on")


def _keys_dir() -> str:
    explicit = os.environ.get("AUTH_LOCAL_KEYS_DIR")
    if explicit:
        return explicit
    # Lazy import — config has filesystem side effects, so only import when needed.
    from config import DATA_DIR
    return os.path.join(str(DATA_DIR), "auth_keys")


def _key_bits() -> int:
    return int(os.environ.get("AUTH_LOCAL_KEY_BITS", "4096"))


def get_keystore() -> Keystore:
    global _keystore
    if _keystore is None:
        with _lock:
            if _keystore is None:
                _keystore = Keystore(_keys_dir(), key_bits=_key_bits())
    return _keystore


def get_code_store() -> CodeStore:
    global _code_store
    if _code_store is None:
        with _lock:
            if _code_store is None:
                _code_store = InMemoryCodeStore()
    return _code_store


def reset_for_tests() -> None:
    global _keystore, _code_store
    with _lock:
        _keystore = None
        _code_store = None


# -------- helpers used by the FastAPI routes --------


def issuer_url(request=None) -> str:
    """The URL this app advertises in OIDC discovery + JWT iss claims.

    Resolution order:
      1. ``AUTH_OIDC_ISSUER`` env — pin the issuer regardless of how the
         request arrived. Required when behind multiple hostnames.
      2. ``APP_BASE_URL`` env.
      3. Derived from ``request.url`` (uvicorn runs with
         ``--forwarded-allow-ips '*'`` in start.sh, so this respects
         X-Forwarded-Proto/Host behind a reverse proxy).
      4. ``http://localhost:8080`` last-resort fallback.
    """
    explicit_issuer = os.environ.get("AUTH_OIDC_ISSUER")
    if explicit_issuer:
        return explicit_issuer.rstrip("/")
    explicit_base = os.environ.get("APP_BASE_URL")
    if explicit_base:
        return explicit_base.rstrip("/")
    if request is not None:
        netloc = request.url.netloc or request.headers.get("host")
        if netloc:
            return f"{request.url.scheme}://{netloc}".rstrip("/")
    return "http://localhost:8080"


def configured_audience() -> str:
    return os.environ.get("AUTH_OIDC_AUDIENCE", "emly-admin-api")


def configured_client_id() -> str:
    return os.environ.get("AUTH_OIDC_CLIENT_ID", "emly-admin-console")


def allowed_redirect_uris(request=None) -> list[str]:
    raw = os.environ.get("AUTH_OIDC_ALLOWED_REDIRECT_URIS", "")
    explicit = [u.strip() for u in raw.split(",") if u.strip()]
    if explicit:
        return explicit
    # Sensible default — the admin UI's callback at whatever URL the
    # request arrived on. The request is the source of truth: if a user
    # hits us at https://emly.example.com, that's where the IdP sent them
    # back, and that's the redirect_uri we expect.
    return [f"{issuer_url(request)}/api/admin/auth/callback"]


def access_token_ttl_seconds() -> int:
    return int(os.environ.get("AUTH_LOCAL_TOKEN_TTL_SECONDS", "3600"))


def lockout_threshold() -> int:
    return int(os.environ.get("AUTH_LOCAL_LOCKOUT_THRESHOLD", "5"))


def lockout_duration_seconds() -> int:
    return int(os.environ.get("AUTH_LOCAL_LOCKOUT_DURATION_SECONDS", str(15 * 60)))
