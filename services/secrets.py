"""Symmetric encryption for bot-scoped secrets and admin session cookies.

Phase 2 of the multi-bot plan stores per-bot LLM API keys (and, in Phase 4,
per-channel OAuth tokens) in the database. Storing them plaintext is a
non-starter — even a brief read-only DB compromise would leak every bot's
provider credentials.

This module is the **only** entry point for encrypting/decrypting those
secrets. The Fernet key comes from ``BOT_SECRETS_KEY`` env (preferred) or
is auto-generated and persisted to ``{DATA_DIR}/.bot_secrets_key`` on
first boot, mirroring the JWT secret pattern in ``services/auth_service``.

The BFF refresh-token cookie (``encrypt_refresh_cookie`` /
``decrypt_refresh_cookie``) uses a Fernet key derived from it for that
purpose only (G6b), as do one-time passcodes (``derive_key``) and widget
tokens (``services/auth/widget_hmac.py``). Fernet is AES-128-CBC with
HMAC-SHA256 authentication.

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
from utils.private_files import write_private

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
    key = Fernet.generate_key()
    write_private(secret_path, key)
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
    global _FERNET, _COOKIE_FERNET
    _FERNET = None
    _COOKIE_FERNET = None


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


# -------- BFF refresh cookie --------
#
# The IdP refresh token is too sensitive for a JS-readable cookie, so it
# lives encrypted in an httpOnly cookie, together with the sign-in time
# (G3a). G6b: the cookie has its own Fernet key, derived from each
# configured root key, so a stored bot secret's ciphertext can't be
# replayed as a cookie (the BFF would decrypt it and send the plaintext
# API key to the IdP as a "refresh token"), and the two can't be
# confused. Cookies encrypted with the root key before this change no
# longer decrypt: those admins sign in again once.

COOKIE_KEY_PURPOSE = "refresh-cookie-v1"
_COOKIE_FERNET: Optional[MultiFernet] = None


def _purpose_fernet_key(root: bytes, purpose: str) -> bytes:
    import base64
    import hashlib
    import hmac

    return base64.urlsafe_b64encode(hmac.new(root, f"emly:{purpose}".encode("utf-8"), hashlib.sha256).digest())


def _cookie_fernet() -> MultiFernet:
    """Encrypts with the current root key's cookie subkey; decrypts with
    the subkey of the current or any previous root key."""
    global _COOKIE_FERNET
    if _COOKIE_FERNET is None:
        _COOKIE_FERNET = MultiFernet([Fernet(_purpose_fernet_key(k, COOKIE_KEY_PURPOSE)) for k in root_keys()])
    return _COOKIE_FERNET


class RefreshCookie:
    """What the BFF refresh cookie holds: the IdP refresh token and the
    time (epoch seconds) the admin signed in (G3a)."""

    __slots__ = ("refresh_token", "auth_time")

    def __init__(self, refresh_token: str, auth_time: float):
        self.refresh_token = refresh_token
        self.auth_time = auth_time

    def __repr__(self) -> str:  # never print the token
        return f"RefreshCookie(auth_time={self.auth_time})"


def encrypt_refresh_cookie(refresh_token: Optional[str], auth_time: float) -> Optional[str]:
    """Encrypt the refresh token together with the sign-in time.
    ``None`` for no token (the caller then sets no cookie)."""
    if not refresh_token:
        return None
    import json

    payload = json.dumps({"v": 1, "rt": refresh_token, "auth_time": int(auth_time)})
    return _cookie_fernet().encrypt(payload.encode("utf-8")).decode("utf-8")


def decrypt_refresh_cookie(ciphertext: Optional[str]) -> Optional[RefreshCookie]:
    """Decrypt a refresh cookie. ``None`` for missing, tampered or
    unreadable input — never raises — so a bad cookie degrades to "sign
    in again" rather than a 500."""
    if not ciphertext:
        return None
    import json

    try:
        data = json.loads(_cookie_fernet().decrypt(ciphertext.encode("utf-8")))
    except InvalidToken:
        log.warning("decrypt_refresh_cookie: invalid ciphertext — treating as missing")
        return None
    except ValueError:
        log.warning("decrypt_refresh_cookie: unreadable payload — treating as missing")
        return None
    if not isinstance(data, dict) or data.get("v") != 1 or not isinstance(data.get("rt"), str) \
            or not data["rt"] or not isinstance(data.get("auth_time"), (int, float)):
        log.warning("decrypt_refresh_cookie: unreadable payload — treating as missing")
        return None
    return RefreshCookie(data["rt"], float(data["auth_time"]))


__all__ = [
    "encrypt",
    "decrypt",
    "derive_key",
    "RefreshCookie",
    "encrypt_refresh_cookie",
    "decrypt_refresh_cookie",
    "InvalidToken",
]
