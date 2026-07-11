"""Generic OIDC provider — the only ``AuthProvider`` implementation.

Speaks OIDC discovery + JWKS to whatever URL ``AUTH_OIDC_ISSUER`` points at.
Whether that URL belongs to this app's embedded issuer or to a hosted IdP
(Auth0, Clerk, Cognito, Keycloak, …) is invisible to this module.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError

from services.auth.base import (
    ExpiredTokenError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidTokenError,
    MissingClaimError,
    OAuthState,
    Principal,
    TokenSet,
)

log = logging.getLogger(__name__)

DEFAULT_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _now() -> int:
    return int(time.time())


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _parse_jwt_header(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise InvalidTokenError("malformed JWT (expected at least 2 segments)")
    try:
        return json.loads(_b64url_decode(parts[0]))
    except Exception as e:
        raise InvalidTokenError(f"malformed JWT header: {e}") from e


class OidcProvider:
    """OIDC verifier + authz/code-exchange client.

    Constructed once per process via ``factory.get_auth_provider()``. Pre-warms
    discovery + JWKS at construction so the first request doesn't pay latency.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        client_id: str,
        client_secret: str | None = None,
        scopes: str = "openid email profile",
        jwks_cache_ttl: int = 3600,
        leeway_seconds: int = 30,
        audience_fallback_to_client_id: bool = False,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._jwks_cache_ttl = jwks_cache_ttl
        self._leeway = leeway_seconds
        self._audience_fallback_to_client_id = audience_fallback_to_client_id

        self._http = http_client or httpx.Client(timeout=DEFAULT_HTTP_TIMEOUT)
        self._jwt_codec = JsonWebToken(["RS256"])
        self._lock = threading.Lock()
        self._jwks_keyset: Any = None
        self._jwks_loaded_at: float = 0.0

        self._discovery: dict[str, Any] = self._fetch_discovery()
        self._refresh_jwks()  # pre-warm

    # -------- discovery + JWKS --------

    def _discovery_url(self) -> str:
        return f"{self._issuer}/.well-known/openid-configuration"

    def _fetch_discovery(self) -> dict[str, Any]:
        url = self._discovery_url()
        log.debug("OIDC: fetching discovery from %s", url)
        try:
            r = self._http.get(url)
        except httpx.RequestError as e:
            raise RuntimeError(
                f"OIDC discovery failed: could not reach {url} ({e!r}). "
                f"Check AUTH_OIDC_ISSUER (should be the issuer root, e.g. "
                f"https://accounts.google.com — not the authorization endpoint) "
                f"and that this process has outbound network access."
            ) from e
        if r.status_code >= 400:
            raise RuntimeError(
                f"OIDC discovery failed: {url} returned HTTP {r.status_code}. "
                f"AUTH_OIDC_ISSUER may be wrong (it must be the issuer root, "
                f"not an authorization or token endpoint). Body: {r.text[:200]!r}"
            )
        doc = r.json()
        doc_issuer = str(doc.get("issuer", "")).rstrip("/")
        if doc_issuer != self._issuer:
            log.warning(
                "OIDC: discovery issuer %r != configured issuer %r — "
                "verification will use the configured value.",
                doc_issuer,
                self._issuer,
            )
        return doc

    def _refresh_jwks(self) -> None:
        jwks_uri = self._discovery.get("jwks_uri")
        if not jwks_uri:
            raise RuntimeError(f"OIDC discovery has no jwks_uri: {self._discovery_url()}")
        r = self._http.get(jwks_uri)
        r.raise_for_status()
        with self._lock:
            self._jwks_keyset = JsonWebKey.import_key_set(r.json())
            self._jwks_loaded_at = time.time()
        log.debug("OIDC: refreshed JWKS from %s", jwks_uri)

    def _maybe_refresh_jwks(self) -> None:
        if time.time() - self._jwks_loaded_at > self._jwks_cache_ttl:
            self._refresh_jwks()

    def _has_kid(self, kid: str) -> bool:
        try:
            self._jwks_keyset.find_by_kid(kid)
            return True
        except (ValueError, KeyError):
            return False

    # -------- token verification --------

    def verify_token(self, token: str) -> Principal:
        if not token or not isinstance(token, str):
            raise InvalidTokenError("missing or non-string token")

        self._maybe_refresh_jwks()

        header = _parse_jwt_header(token)
        kid = header.get("kid")
        if kid and not self._has_kid(kid):
            self._refresh_jwks()
            if not self._has_kid(kid):
                raise InvalidTokenError(f"unknown signing key: kid={kid!r}")

        try:
            claims = self._jwt_codec.decode(token, key=self._jwks_keyset)
        except JoseError as e:
            raise InvalidTokenError(f"jwt decode failed: {e}") from e

        claims_dict = dict(claims)
        self._validate_claims(claims_dict)
        return self._principal_from_claims(claims_dict)

    def _validate_claims(self, claims: dict[str, Any]) -> None:
        now = _now()

        iss = claims.get("iss")
        if not iss or str(iss).rstrip("/") != self._issuer:
            raise InvalidIssuerError(f"iss {iss!r} does not match {self._issuer!r}")

        aud = claims.get("aud")
        aud_list = [aud] if isinstance(aud, str) else list(aud or [])
        accepted = {self._audience}
        if self._audience_fallback_to_client_id:
            accepted.add(self._client_id)
        if not (set(aud_list) & accepted):
            raise InvalidAudienceError(f"aud {aud!r} does not include any of {accepted!r}")

        exp = claims.get("exp")
        if exp is None or int(exp) + self._leeway < now:
            raise ExpiredTokenError(f"exp={exp} now={now} leeway={self._leeway}")

        nbf = claims.get("nbf")
        if nbf is not None and int(nbf) - self._leeway > now:
            raise InvalidTokenError(f"token not yet valid: nbf={nbf} now={now}")

    def _principal_from_claims(self, claims: dict[str, Any]) -> Principal:
        sub = claims.get("sub")
        if not sub:
            raise MissingClaimError("token missing `sub`")
        email = claims.get("email")
        if not email:
            raise MissingClaimError("token missing `email`")
        return Principal(
            issuer=self._issuer,
            subject=str(sub),
            email=str(email),
            # Trust the IdP: a successful OIDC sign-in proves the user controls
            # the address. IdPs vary in whether they emit `email_verified` and
            # what value they put there (Okta's Org Auth Server, for example,
            # often emits False or omits it for activated users). Gating on the
            # raw claim creates spurious rejections, so we treat OIDC auth as
            # implicit email verification.
            email_verified=True,
            name=str(claims["name"]) if claims.get("name") else None,
            raw_claims=claims,
        )

    # -------- authorize / token / logout --------

    def authorize_url(self, oauth_state: OAuthState, redirect_uri: str) -> str:
        endpoint = self._discovery.get("authorization_endpoint")
        if not endpoint:
            raise RuntimeError("OIDC discovery has no authorization_endpoint")
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": self._scopes,
            "state": oauth_state.state,
            "nonce": oauth_state.nonce,
            "code_challenge": _pkce_challenge(oauth_state.code_verifier),
            "code_challenge_method": "S256",
        }
        return f"{endpoint}?{urlencode(params)}"

    def exchange_code(
        self,
        code: str,
        oauth_state: OAuthState,
        redirect_uri: str,
    ) -> tuple[TokenSet, Principal]:
        endpoint = self._discovery.get("token_endpoint")
        if not endpoint:
            raise RuntimeError("OIDC discovery has no token_endpoint")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._client_id,
            "code_verifier": oauth_state.code_verifier,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret
        r = self._http.post(endpoint, data=data)
        if r.status_code >= 400:
            raise InvalidTokenError(
                f"token endpoint returned {r.status_code}: {r.text[:200]}"
            )
        body = r.json()

        access_token = body.get("access_token")
        id_token = body.get("id_token")
        refresh_token = body.get("refresh_token")
        expires_in = int(body.get("expires_in", 3600))
        expires_at = datetime.fromtimestamp(_now() + expires_in, tz=timezone.utc)

        if not access_token:
            raise InvalidTokenError("token response missing access_token")
        if not id_token:
            raise InvalidTokenError("token response missing id_token (OIDC requires it)")

        principal = self.verify_token(id_token)
        id_nonce = principal.raw_claims.get("nonce")
        if id_nonce != oauth_state.nonce:
            raise InvalidTokenError(f"id_token nonce mismatch: got {id_nonce!r}")

        return (
            TokenSet(
                access_token=access_token,
                id_token=id_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
            ),
            principal,
        )

    def logout_url(self, redirect_uri: str | None) -> str | None:
        endpoint = self._discovery.get("end_session_endpoint")
        if not endpoint:
            return None
        if redirect_uri is None:
            return endpoint
        return f"{endpoint}?{urlencode({'post_logout_redirect_uri': redirect_uri, 'client_id': self._client_id})}"


# -------- helpers (also used by routes/auth.py) --------


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_pkce_verifier() -> str:
    """43-char URL-safe verifier (RFC 7636 §4.1)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    return secrets.token_urlsafe(16)
