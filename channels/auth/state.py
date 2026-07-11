"""CSRF state token for OAuth /install → /oauth/callback redirects.

State payload is HMAC-signed JSON with a TTL and a one-time-use nonce.
The signing key piggybacks on ``BOT_SECRETS_KEY`` so we don't add yet
another secret env var; the Fernet derivation is fine for HMAC purposes
(we use the raw key bytes as the HMAC key).

Replay defense: a small in-process LRU records the nonces we've seen
inside the TTL window — second use of a nonce → reject. This catches
the obvious "reuse the redirect link" attack; a multi-replica deploy
needs Redis-backed replay store, which is part of the future-items
queue work.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections import OrderedDict
from typing import Optional

from config import DATA_DIR

log = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 600
_REPLAY_LRU_SIZE = 4096
_REPLAY_LRU: "OrderedDict[str, float]" = OrderedDict()


class InvalidRedirect(ValueError):
    """``redirect_to`` failed the absolute-path safety check."""


def validate_redirect_to(redirect_to: Optional[str]) -> str:
    """Reject anything that could turn the post-callback redirect into
    a phishing vector.

    A safe ``redirect_to`` is either empty (we'll default to ``/``) or
    an absolute path on the same origin: it MUST start with a single
    ``/`` and MUST NOT start with ``//`` (which browsers interpret as
    ``//attacker.example/`` — a scheme-relative URL). We also reject
    anything containing a scheme (``://``) or whitespace.

    Validation runs at *issue* time so the HMAC seals an already-safe
    value; the callback then trusts the signed payload without
    re-checking.
    """
    if not redirect_to:
        return ""
    if redirect_to.startswith("//"):
        raise InvalidRedirect("redirect_to must not start with '//' (scheme-relative URLs are unsafe)")
    if not redirect_to.startswith("/"):
        raise InvalidRedirect("redirect_to must be an absolute path beginning with '/'")
    if "://" in redirect_to or any(c in redirect_to for c in (" ", "\t", "\n", "\r")):
        raise InvalidRedirect("redirect_to must be a same-origin absolute path")
    return redirect_to


def _signing_key() -> bytes:
    env_key = os.environ.get("BOT_SECRETS_KEY")
    if env_key:
        return env_key.encode("utf-8")
    secret_path = os.path.join(str(DATA_DIR), ".bot_secrets_key")
    if os.path.exists(secret_path):
        with open(secret_path, "rb") as fh:
            return fh.read().strip()
    raise RuntimeError("BOT_SECRETS_KEY unavailable; cannot sign OAuth state")


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue(payload: dict, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    body = dict(payload)
    body.setdefault("nonce", secrets.token_urlsafe(16))
    body["iat"] = int(time.time())
    body["exp"] = body["iat"] + ttl_seconds
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
    return f"{_b64u(raw)}.{_b64u(sig)}"


def verify(state: str) -> Optional[dict]:
    try:
        body_b64, sig_b64 = state.split(".", 1)
        raw = _b64u_decode(body_b64)
        sig = _b64u_decode(sig_b64)
    except (ValueError, Exception):
        return None
    expected = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if int(body.get("exp", 0)) < int(time.time()):
        return None
    nonce = body.get("nonce")
    if not nonce:
        return None
    now = time.time()
    cutoff = now - DEFAULT_TTL_SECONDS - 60
    while _REPLAY_LRU and next(iter(_REPLAY_LRU.values())) < cutoff:
        _REPLAY_LRU.popitem(last=False)
    if nonce in _REPLAY_LRU:
        log.warning("OAuth state nonce replayed: %s", nonce)
        return None
    _REPLAY_LRU[nonce] = now
    if len(_REPLAY_LRU) > _REPLAY_LRU_SIZE:
        _REPLAY_LRU.popitem(last=False)
    return body
