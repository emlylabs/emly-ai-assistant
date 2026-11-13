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

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from config import DATA_DIR

log = logging.getLogger(__name__)

_SECRET_FILENAME = ".bot_secrets_key"


def _load_or_create_key() -> bytes:
    """The current key: ``BOT_SECRETS_KEY``, or (outside production only)
    a key generated once and kept in ``{DATA_DIR}/.bot_secrets_key``."""
    env_key = (os.environ.get("BOT_SECRETS_KEY") or "").strip()
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


def previous_keys() -> list[bytes]:
    """Retired keys from ``BOT_SECRETS_KEY_PREVIOUS`` (comma-separated,
    newest first). They still decrypt and verify but never encrypt."""
    raw = os.environ.get("BOT_SECRETS_KEY_PREVIOUS") or ""
    return [k.strip().encode("utf-8") for k in raw.split(",") if k.strip()]


def root_keys() -> list[bytes]:
    """Current key first, then previous keys."""
    return [_load_or_create_key(), *previous_keys()]


def validate_configuration() -> None:
    """Boot check (C8). In production ``BOT_SECRETS_KEY`` is required — a key
    generated into DATA_DIR is lost with the volume and can't be rotated or
    backed up separately. Every configured key must be a valid Fernet key."""
    from utils.runtime import is_prod_mode

    if is_prod_mode() and not (os.environ.get("BOT_SECRETS_KEY") or "").strip():
        hint = ""
        existing = os.path.join(str(DATA_DIR), _SECRET_FILENAME)
        if os.path.exists(existing):
            hint = (f" A key generated earlier exists at {existing}; set BOT_SECRETS_KEY to its "
                    "contents so already-encrypted secrets stay readable.")
        raise RuntimeError(
            "MODE=prod requires BOT_SECRETS_KEY (a Fernet key: python -c \"from cryptography.fernet "
            "import Fernet; print(Fernet.generate_key().decode())\")." + hint
        )
    for name, key in (("BOT_SECRETS_KEY", (os.environ.get("BOT_SECRETS_KEY") or "").strip().encode()),
                      *(("BOT_SECRETS_KEY_PREVIOUS", k) for k in previous_keys())):
        if not key:
            continue
        try:
            Fernet(key)
        except Exception:
            raise RuntimeError(f"{name} is not a valid Fernet key (32 url-safe base64-encoded bytes).") from None


_FERNET: Optional[MultiFernet] = None


def _get_fernet() -> MultiFernet:
    """Encrypts with the current key; decrypts with the current or any
    previous key, so rotation doesn't break stored secrets or cookies."""
    global _FERNET
    if _FERNET is None:
        _FERNET = MultiFernet([Fernet(k) for k in root_keys()])
    return _FERNET


def reset_for_tests() -> None:
    global _FERNET
    _FERNET = None


def rotate(ciphertext: Optional[str]) -> Optional[str]:
    """Re-encrypt a stored value under the current key (decrypting with
    whichever key it was written under). Raises ``InvalidToken`` if no
    configured key can read it."""
    if not ciphertext:
        return ciphertext
    return _get_fernet().rotate(ciphertext.encode("utf-8")).decode("utf-8")


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


def derive_key(purpose: str) -> bytes:
    """Derive a 32-byte purpose-specific key from ``BOT_SECRETS_KEY``.

    Used where a MAC key is needed (e.g. hashing one-time passcodes) so the
    Fernet key itself is never reused directly for a second purpose.
    """
    import hashlib
    import hmac

    return hmac.new(_load_or_create_key(), f"emly:{purpose}".encode("utf-8"), hashlib.sha256).digest()


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
