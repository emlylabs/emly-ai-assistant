"""Per-bot widget-token issuer + verifier.

Replaces trust in the spoofable ``X-Emly-UserID`` / ``X-Emly-SessionID``
headers on widget surfaces. Each bot has a versioned HKDF subkey of
``BOT_SECRETS_KEY``; tokens carry ``(bot_id, user_id, session_id, key_version,
exp)`` plus an HMAC over the payload.

Tokens are intentionally stateless (no DB lookup on verify) and short
(small enough to fit in a query param or `Authorization` header without
size pain).

Format (compact, dot-separated, base64url):

    base64url(json(payload)) "." base64url(hmac_sha256(key, header))

Key rotation:
    ``Bots.rotate_widget_key(bot_id)`` increments ``widget_key_version`` and
    stamps ``widget_key_rotated_at``. The verifier accepts the previous
    version for ``WIDGET_TOKEN_ROTATION_GRACE_SECONDS`` after rotation so
    in-flight conversations don't break mid-flight.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.secrets import _load_or_create_key as _bot_root_key  # type: ignore[attr-defined]

log = logging.getLogger(__name__)


WIDGET_TOKEN_INFO = b"widget-token-v1"


def _ttl_seconds() -> int:
    return int(os.environ.get("WIDGET_TOKEN_TTL_SECONDS", str(24 * 3600)))


def _grace_seconds() -> int:
    return int(os.environ.get("WIDGET_TOKEN_ROTATION_GRACE_SECONDS", str(24 * 3600)))


@dataclass(frozen=True)
class WidgetPrincipal:
    """Identity carried by a verified widget token."""

    bot_id: str
    user_id: str
    session_id: str
    key_version: int
    issued_at: int
    expires_at: int


class WidgetTokenError(Exception):
    """Token failed to verify. Routes map to 401."""


# -------- HKDF --------


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    out = b""
    t = b""
    counter = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        out += t
        counter += 1
    return out[:length]


def _derive_bot_key(bot_id: str, key_version: int) -> bytes:
    """Derive the per-bot HMAC key for the given version. Pure, no I/O."""
    salt = f"{bot_id}:v{key_version}".encode("utf-8")
    prk = _hkdf_extract(salt, _bot_root_key())
    return _hkdf_expand(prk, WIDGET_TOKEN_INFO, length=32)


# -------- token codec --------


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _now() -> int:
    return int(time.time())


def issue(
    *,
    bot_id: str,
    user_id: str,
    session_id: str,
    key_version: int,
    ttl_seconds: int | None = None,
) -> str:
    """Mint a widget token for the given user/session on this bot+version."""
    ttl = ttl_seconds if ttl_seconds is not None else _ttl_seconds()
    now = _now()
    payload: dict[str, Any] = {
        "bot": bot_id,
        "u": user_id,
        "s": session_id,
        "v": key_version,
        "iat": now,
        "exp": now + ttl,
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    key = _derive_bot_key(bot_id, key_version)
    sig = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def verify(
    token: str,
    *,
    expected_bot_id: str,
    current_key_version: int,
    rotated_at: datetime | None = None,
) -> WidgetPrincipal:
    """Verify the token. Raises ``WidgetTokenError`` on any failure.

    Accepts the current ``key_version`` and (within the rotation grace window)
    the previous version. Rejects token from any other version, with mismatched
    bot_id, with a tampered payload, or past ``exp``.
    """
    if not token or "." not in token:
        raise WidgetTokenError("malformed_token")
    payload_b64, sig_b64 = token.rsplit(".", 1)
    try:
        payload = json.loads(_b64url_decode(payload_b64))
        sig = _b64url_decode(sig_b64)
    except Exception as e:
        raise WidgetTokenError(f"malformed_token: {e}") from e

    bot_id = payload.get("bot")
    user_id = payload.get("u")
    session_id = payload.get("s")
    key_version = payload.get("v")
    iat = payload.get("iat")
    exp = payload.get("exp")
    if not all(isinstance(x, (str, int)) and x is not None for x in (bot_id, user_id, session_id)):
        raise WidgetTokenError("missing_claims")
    if not isinstance(key_version, int) or not isinstance(iat, int) or not isinstance(exp, int):
        raise WidgetTokenError("malformed_claims")
    if bot_id != expected_bot_id:
        raise WidgetTokenError("bot_mismatch")
    if exp < _now():
        raise WidgetTokenError("expired")

    accepted_versions = {current_key_version}
    if rotated_at is not None and key_version == current_key_version - 1:
        # In the grace window? `rotated_at` records when we bumped to current.
        grace_until = rotated_at + _timedelta(seconds=_grace_seconds())
        if grace_until >= datetime.now(timezone.utc):
            accepted_versions.add(key_version)
    if key_version not in accepted_versions:
        raise WidgetTokenError("stale_key_version")

    expected_sig = hmac.new(
        _derive_bot_key(bot_id, key_version),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected_sig, sig):
        raise WidgetTokenError("bad_signature")

    return WidgetPrincipal(
        bot_id=bot_id,
        user_id=user_id,
        session_id=session_id,
        key_version=key_version,
        issued_at=iat,
        expires_at=exp,
    )


# -------- helper imported for clarity --------


def _timedelta(*, seconds: int):
    from datetime import timedelta
    return timedelta(seconds=seconds)
