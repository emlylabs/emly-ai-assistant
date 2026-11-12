"""Symmetric encryption for bot-scoped secrets and admin session cookies.

Phase 2 of the multi-bot plan stores per-bot LLM API keys (and, in Phase 4,
per-channel OAuth tokens) in the database. Storing them plaintext is a
non-starter — even a brief read-only DB compromise would leak every bot's
provider credentials.

This module is the **only** entry point for encrypting/decrypting those
secrets. The Fernet key comes from ``BOT_SECRETS_KEY`` env (preferred) or
is auto-generated and persisted to ``{DATA_DIR}/.bot_secrets_key`` on
first boot, mirroring the JWT secret pattern in ``services/auth_service``.

The same key is reused to encrypt the BFF refresh-token cookie payload
(``encrypt_session_cookie`` / ``decrypt_session_cookie``) — the BFF path
keeps the refresh_token out of the JavaScript surface by storing it
encrypted in an httpOnly cookie. The encryption is symmetric AES-128
in CBC mode with HMAC-SHA256 authentication (Fernet's standard
construction); the key is process-local, so an attacker would need
both the cookie and the on-disk key to recover the refresh_token.

This is "good enough for v1" — Fernet with a key on disk raises the bar
above plaintext but is not real protection if an attacker has both the DB
and the host disk. The plan's Future-Items section spells out the
KMS-backed envelope-encryption upgrade path; the abstraction stays the
same, only the backend changes.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from config import DATA_DIR

log = logging.getLogger(__name__)

_SECRET_FILENAME = ".bot_secrets_key"


def _load_or_create_key() -> bytes:
    env_key = os.environ.get("BOT_SECRETS_KEY")
    if env_key:
        return env_key.encode("utf-8")

    secret_path = os.path.join(str(DATA_DIR), _SECRET_FILENAME)
    if os.path.exists(secret_path):
        with open(secret_path, "rb") as fh:
            return fh.read().strip()

    log.warning(
        "BOT_SECRETS_KEY not set — generating one and persisting it to %s. "
        "Set BOT_SECRETS_KEY in production so the key survives DATA_DIR rebuilds.",
        secret_path,
    )
    os.makedirs(str(DATA_DIR), exist_ok=True)
    key = Fernet.generate_key()
    with open(secret_path, "wb") as fh:
        fh.write(key)
    os.chmod(secret_path, 0o600)
    return key


_FERNET: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _FERNET
    if _FERNET is None:
        _FERNET = Fernet(_load_or_create_key())
    return _FERNET


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a string. Pass ``None`` through unchanged."""
    if plaintext is None or plaintext == "":
        return None
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: Optional[str]) -> Optional[str]:
    """Decrypt a string. ``None`` / empty pass through; tampered data
    raises ``InvalidToken`` so callers can fail loud rather than serve
    garbage downstream.
    """
    if ciphertext is None or ciphertext == "":
        return None
    return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# -------- BFF session-cookie helpers --------
#
# The refresh_token issued by the IdP is too sensitive to put in a JS-readable
# cookie. We store it in a separate httpOnly cookie, encrypted with the same
# symmetric key as the per-bot secrets. The cookie name is configured
# independently of the session cookie (env: AUTH_REFRESH_COOKIE_NAME), but the
# value-format lives entirely in this module.

def encrypt_session_cookie(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a refresh_token for the BFF refresh-cookie.

    Returns ``None`` if the input is ``None`` / empty (so the caller can
    skip setting the cookie in that case). The output is ASCII-safe
    (Fernet ciphertext is base64url) so it round-trips through any
    cookie spec.
    """
    return encrypt(plaintext)


def decrypt_session_cookie(ciphertext: Optional[str]) -> Optional[str]:
    """Decrypt a refresh_token cookie. Returns ``None`` on missing or
    tampered input — never raises — so a corrupt cookie cleanly
    degrades to "force a fresh login" rather than 500-ing the request.
    """
    if not ciphertext:
        return None
    try:
        return decrypt(ciphertext)
    except InvalidToken:
        log.warning("decrypt_session_cookie: invalid ciphertext — treating as missing")
        return None


__all__ = [
    "encrypt",
    "decrypt",
    "encrypt_session_cookie",
    "decrypt_session_cookie",
    "InvalidToken",
]
