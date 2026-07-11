"""Mint RS256 JWTs signed by the keystore's current key.

Used by the embedded OIDC issuer's token endpoint and by the bootstrap flow.
External IdPs do their own signing; this module is irrelevant to them — the
verifier (``services/auth/oidc.py``) consumes both.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from authlib.jose import JsonWebToken

from services.auth.issuer.keystore import Keystore


def _now() -> int:
    return int(time.time())


def _generate_jti() -> str:
    return secrets.token_urlsafe(16)


def mint_access_token(
    *,
    keystore: Keystore,
    issuer: str,
    audience: str,
    subject: str,
    email: str,
    email_verified: bool,
    ttl_seconds: int,
    extra: dict[str, Any] | None = None,
) -> str:
    """Sign a standard OAuth2 access token (RS256, JWT format)."""
    now = _now()
    claims: dict[str, Any] = {
        "iss": issuer.rstrip("/"),
        "aud": audience,
        "sub": subject,
        "email": email,
        "email_verified": email_verified,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": _generate_jti(),
    }
    if extra:
        claims.update(extra)
    return _sign(keystore, claims)


def mint_id_token(
    *,
    keystore: Keystore,
    issuer: str,
    audience: str,
    subject: str,
    email: str,
    email_verified: bool,
    nonce: str | None,
    ttl_seconds: int,
    name: str | None = None,
) -> str:
    """Sign an OIDC id_token with the same shape as access_token plus ``nonce`` and optional ``name``."""
    now = _now()
    claims: dict[str, Any] = {
        "iss": issuer.rstrip("/"),
        "aud": audience,
        "sub": subject,
        "email": email,
        "email_verified": email_verified,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": _generate_jti(),
    }
    if nonce is not None:
        claims["nonce"] = nonce
    if name:
        claims["name"] = name
    return _sign(keystore, claims)


def _sign(keystore: Keystore, claims: dict[str, Any]) -> str:
    header = {"alg": "RS256", "kid": keystore.current_kid(), "typ": "JWT"}
    raw = JsonWebToken(["RS256"]).encode(header, claims, keystore.current_private_pem())
    return raw.decode("ascii") if isinstance(raw, bytes) else raw
