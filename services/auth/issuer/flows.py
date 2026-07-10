"""Authorization-code-with-PKCE dance for the embedded OIDC issuer.

This module is the pure-logic layer. ``routes/auth_issuer.py`` is the FastAPI
glue that wires it into HTTP. Everything here is deterministic and unit-testable
without a running app.

The flow:

1. Browser → ``GET /api/auth/local/authorize?…`` with ``state``,
   ``code_challenge``, ``redirect_uri``, ``client_id``, ``scope``, ``nonce``.
   We store the request, render a login form.
2. User submits credentials. We verify against ``local_credential``.
   On success we mint a one-time ``code`` (opaque string) and persist
   the ``(code → CodeRecord)`` mapping in ``CodeStore``. 302 to
   ``redirect_uri?code=<code>&state=<state>``.
3. Browser → app callback → ``POST /api/auth/local/token`` with ``code``
   and ``code_verifier``. We pop the code, verify ``code_verifier`` matches
   the original ``code_challenge``, and mint access + id tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

# RFC 3986 unreserved characters — the safest set for opaque OAuth
# tokens that are interpolated into HTML attribute context. Rejecting
# anything else stops `"`, `<`, `>`, `/` etc. from reaching the form
# renderer in the first place; the renderer also HTML-escapes as a
# second layer (see ``_render_authorize_form``).
_OAUTH_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
# DoS guard. 4096 is well above anything the spec allows for state /
# nonce / code_challenge in practice; we don't bother with a min length
# because character-class validation already excludes empty / unsafe.
_OAUTH_TOKEN_MAX_LEN = 4096

log = logging.getLogger(__name__)

DEFAULT_CODE_TTL = timedelta(minutes=1)
DEFAULT_LOCKOUT_THRESHOLD = 5
DEFAULT_LOCKOUT_DURATION_SECONDS = 15 * 60


@dataclass(frozen=True)
class AuthorizeRequest:
    """Validated query params from the start of the authorize dance."""

    client_id: str
    redirect_uri: str
    state: str
    nonce: str | None
    code_challenge: str
    code_challenge_method: str  # always "S256"
    scope: str


@dataclass(frozen=True)
class CodeRecord:
    """Per-issued-code state. Single-use; popped at token exchange."""

    code: str
    admin_id: str
    email: str
    email_verified: bool
    name: str | None
    redirect_uri: str
    code_challenge: str
    nonce: str | None
    scope: str
    issued_at: datetime


class CodeStore(Protocol):
    def put(self, record: CodeRecord, ttl: timedelta = DEFAULT_CODE_TTL) -> None: ...
    def pop(self, code: str) -> CodeRecord | None: ...


class InMemoryCodeStore:
    """Single-replica in-memory store with lazy expiry on pop."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[CodeRecord, datetime]] = {}
        self._lock = threading.Lock()

    def put(self, record: CodeRecord, ttl: timedelta = DEFAULT_CODE_TTL) -> None:
        expires_at = datetime.now(timezone.utc) + ttl
        with self._lock:
            self._items[record.code] = (record, expires_at)

    def pop(self, code: str) -> CodeRecord | None:
        now = datetime.now(timezone.utc)
        with self._lock:
            entry = self._items.pop(code, None)
        if entry is None:
            return None
        record, expires_at = entry
        if expires_at < now:
            return None
        return record


# -------- helpers --------


def generate_code() -> str:
    return secrets.token_urlsafe(32)


def verify_pkce(code_verifier: str, code_challenge: str, method: str = "S256") -> bool:
    """RFC 7636 §4.6 — recompute the challenge from the verifier and compare."""
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(expected, code_challenge)


# -------- domain errors --------


class FlowError(Exception):
    """Caller maps this to a 4xx HTTP response."""


class InvalidAuthorizeRequest(FlowError):
    """The query params don't form a valid authorize request."""


def _check_opaque_token(field: str, value: str) -> None:
    """Reject opaque OAuth tokens whose contents could escape an HTML
    attribute. The form renderer also escapes, but rejecting at the
    door means malformed values never reach the template at all."""
    if len(value) > _OAUTH_TOKEN_MAX_LEN:
        raise InvalidAuthorizeRequest(
            f"{field} exceeds {_OAUTH_TOKEN_MAX_LEN}-character limit"
        )
    if not _OAUTH_OPAQUE_TOKEN_RE.match(value):
        raise InvalidAuthorizeRequest(
            f"{field} contains characters that are not allowed (must match [A-Za-z0-9._~-]+)"
        )


class UnknownClientError(FlowError):
    """``client_id`` doesn't match the configured embedded-issuer client."""


class InvalidRedirectUriError(FlowError):
    """``redirect_uri`` not in the allowlist."""


class CredentialError(FlowError):
    """Email or password didn't verify."""


class AccountLockedError(FlowError):
    """Too many failed attempts; account temporarily locked."""


class CodeExchangeError(FlowError):
    """Code missing/expired or PKCE verifier doesn't match."""


# -------- core flow methods --------


def parse_authorize_request(
    *,
    client_id: str | None,
    redirect_uri: str | None,
    state: str | None,
    nonce: str | None,
    code_challenge: str | None,
    code_challenge_method: str | None,
    response_type: str | None,
    scope: str | None,
) -> AuthorizeRequest:
    """Validate the authorize-endpoint query params; raise on any deviation."""
    if not client_id:
        raise InvalidAuthorizeRequest("missing client_id")
    if not redirect_uri:
        raise InvalidAuthorizeRequest("missing redirect_uri")
    if not state:
        raise InvalidAuthorizeRequest("missing state")
    _check_opaque_token("state", state)
    if not code_challenge:
        raise InvalidAuthorizeRequest("missing code_challenge")
    _check_opaque_token("code_challenge", code_challenge)
    if nonce is not None and nonce != "":
        _check_opaque_token("nonce", nonce)
    if code_challenge_method != "S256":
        raise InvalidAuthorizeRequest(f"unsupported code_challenge_method: {code_challenge_method!r}")
    if response_type != "code":
        raise InvalidAuthorizeRequest(f"unsupported response_type: {response_type!r}")
    return AuthorizeRequest(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope or "openid email profile",
    )


def check_client(authz: AuthorizeRequest, *, configured_client_id: str, allowed_redirect_uris: list[str]) -> None:
    if authz.client_id != configured_client_id:
        raise UnknownClientError(f"unknown client_id: {authz.client_id!r}")
    if authz.redirect_uri not in allowed_redirect_uris:
        raise InvalidRedirectUriError(f"redirect_uri not allowed: {authz.redirect_uri!r}")


@dataclass(frozen=True)
class CredentialCheck:
    """The result of authenticating an email+password against ``local_credential``."""

    admin_id: str
    must_change: bool


def issue_code(
    *,
    code_store: CodeStore,
    authz: AuthorizeRequest,
    admin_id: str,
    email: str,
    email_verified: bool,
    name: Optional[str] = None,
) -> str:
    """Mint a one-time code for the given authenticated user and persist it."""
    code = generate_code()
    record = CodeRecord(
        code=code,
        admin_id=admin_id,
        email=email,
        email_verified=email_verified,
        name=name,
        redirect_uri=authz.redirect_uri,
        code_challenge=authz.code_challenge,
        nonce=authz.nonce,
        scope=authz.scope,
        issued_at=datetime.now(timezone.utc),
    )
    code_store.put(record)
    return code


def consume_code(
    *,
    code_store: CodeStore,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> CodeRecord:
    """Pop the code, verify PKCE + redirect_uri, and return the record.

    Raises ``CodeExchangeError`` on any failure. The store ``pop`` is one-shot
    so a replay attempt always fails.
    """
    if not code or not code_verifier:
        raise CodeExchangeError("missing code or code_verifier")
    record = code_store.pop(code)
    if record is None:
        raise CodeExchangeError("code is invalid or expired")
    if record.redirect_uri != redirect_uri:
        raise CodeExchangeError("redirect_uri does not match the original authorize request")
    if not verify_pkce(code_verifier, record.code_challenge):
        raise CodeExchangeError("PKCE verifier does not match challenge")
    return record
