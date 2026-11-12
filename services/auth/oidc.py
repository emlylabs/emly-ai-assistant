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
from typing import Any, Callable, Optional
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
        trust_email_unconditionally: bool = False,
        http_client: httpx.Client | None = None,
        # In-process injection used when the issuer is this same app's
        # embedded one — avoids a self-loopback HTTP call that DNS often
        # can't resolve from inside the pod (the external hostname isn't
        # reachable from within the cluster).
        discovery_override: Optional[dict[str, Any]] = None,
        jwks_provider: Optional[Callable[[], dict[str, Any]]] = None,
        token_exchanger: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._jwks_cache_ttl = jwks_cache_ttl
        self._leeway = leeway_seconds
        self._audience_fallback_to_client_id = audience_fallback_to_client_id
        # When True (embedded issuer, or explicit opt-in for IdPs known to
        # always verify), ``email_verified`` is forced to True regardless
        # of the claim. When False (hosted IdPs by default), we surface
        # the actual claim and let ``user_resolver`` refuse activation.
        self._trust_email_unconditionally = trust_email_unconditionally

        self._http = http_client or httpx.Client(timeout=DEFAULT_HTTP_TIMEOUT)
        self._jwt_codec = JsonWebToken(["RS256"])
        self._lock = threading.Lock()
        self._jwks_keyset: Any = None
        self._jwks_loaded_at: float = 0.0
        self._jwks_provider = jwks_provider
        self._token_exchanger = token_exchanger

        self._discovery: dict[str, Any] = (
            discovery_override if discovery_override is not None else self._fetch_discovery()
        )
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
        # Embedded-issuer fast path: read the keystore in-process so the
        # pod doesn't have to resolve its own external hostname to fetch
        # its own JWKS.
        if self._jwks_provider is not None:
            jwks_doc = self._jwks_provider()
            with self._lock:
                self._jwks_keyset = JsonWebKey.import_key_set(jwks_doc)
                self._jwks_loaded_at = time.time()
            log.debug("OIDC: refreshed JWKS from in-process provider")
            return

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
        # For the embedded issuer (or an IdP the operator has explicitly
        # marked trusted via env), a successful OIDC sign-in proves the
        # user controls the address — we own the password gate. For
        # other IdPs we pass the claim through so ``user_resolver`` can
        # refuse activation if verification didn't happen at the IdP.
        if self._trust_email_unconditionally:
            email_verified = True
        else:
            email_verified = _coerce_email_verified(claims.get("email_verified"))
        return Principal(
            issuer=self._issuer,
            subject=str(sub),
            email=str(email),
            email_verified=email_verified,
            name=str(claims["name"]) if claims.get("name") else None,
            raw_claims=claims,
        )

    # -------- authorize / token / logout --------

    def authorize_url(
        self,
        oauth_state: OAuthState,
        redirect_uri: str,
        *,
        state_override: Optional[str] = None,
    ) -> str:
        """Build the IdP authorize URL.

        ``state_override``, when supplied, replaces ``oauth_state.state`` in
        the URL's ``state`` parameter. The route uses this to wrap its inner
        state in a gateway HMAC envelope while keeping the server-side state
        store keyed by the original inner state (what the gateway forwards
        back on the callback).

        We always append ``offline_access`` to the requested scope so the
        IdP returns a ``refresh_token`` alongside the id_token. This is the
        OIDC-defined scope that gates refresh-token issuance (RFC 6749 §6).
        Some IdPs (Auth0 in particular) only honour it after a consent
        screen — for the admin auth flow this is acceptable: the first
        login shows the consent pane once, subsequent logins reuse the
        existing consent grant.
        """
        endpoint = self._discovery.get("authorization_endpoint")
        if not endpoint:
            raise RuntimeError("OIDC discovery has no authorization_endpoint")
        scope_tokens = self._scopes.split()
        if "offline_access" not in scope_tokens:
            scope_tokens.append("offline_access")
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scope_tokens),
            "state": state_override if state_override is not None else oauth_state.state,
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

        # Embedded-issuer fast path: call the local token flow in-process so
        # the pod doesn't have to dial its own external hostname.
        if self._token_exchanger is not None:
            try:
                body = self._token_exchanger(data)
            except InvalidTokenError:
                raise
            except Exception as e:
                raise InvalidTokenError(f"local token exchange failed: {e}") from e
        else:
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

    def refresh_tokens(self, refresh_token: str) -> TokenSet:
        """Use a refresh_token to mint a fresh access + id token from the IdP.

        Implements the OAuth 2.0 ``refresh_token`` grant (RFC 6749 §6).
        Some IdPs rotate the refresh_token on every refresh (Auth0, Keycloak
        with rotation enabled) — when the response carries a new
        ``refresh_token`` we surface it on the returned ``TokenSet``;
        otherwise the caller keeps using the existing refresh_token.

        The id_token is verified (signature + standard claims) before being
        returned, so a token-refresh call doubles as a "still trusted?"
        probe — if the IdP has revoked the session, the id_token will fail
        signature/claim validation and ``InvalidTokenError`` will surface.

        Raises ``InvalidTokenError`` on any failure (bad refresh_token,
        expired, IdP-down, etc.). The caller is responsible for clearing
        the session and redirecting to the login flow on error.
        """
        if not refresh_token or not isinstance(refresh_token, str):
            raise InvalidTokenError("missing or non-string refresh_token")

        endpoint = self._discovery.get("token_endpoint")
        if not endpoint:
            raise RuntimeError("OIDC discovery has no token_endpoint")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret

        # Embedded-issuer fast path: call the local token flow in-process so
        # the pod doesn't have to dial its own external hostname.
        if self._token_exchanger is not None:
            try:
                body = self._token_exchanger(data)
            except InvalidTokenError:
                raise
            except Exception as e:
                raise InvalidTokenError(f"local token refresh failed: {e}") from e
        else:
            r = self._http.post(endpoint, data=data)
            if r.status_code >= 400:
                raise InvalidTokenError(
                    f"token refresh endpoint returned {r.status_code}: {r.text[:200]}"
                )
            body = r.json()

        access_token = body.get("access_token")
        id_token = body.get("id_token")
        new_refresh_token = body.get("refresh_token") or refresh_token
        expires_in = int(body.get("expires_in", 3600))
        expires_at = datetime.fromtimestamp(_now() + expires_in, tz=timezone.utc)

        if not access_token:
            raise InvalidTokenError("token refresh response missing access_token")
        if not id_token:
            raise InvalidTokenError("token refresh response missing id_token (OIDC requires it)")

        # Verify the freshly-minted id_token so a tampered/forged response
        # is rejected before the caller ever writes the new cookie.
        self.verify_token(id_token)

        return TokenSet(
            access_token=access_token,
            id_token=id_token,
            refresh_token=new_refresh_token,
            expires_at=expires_at,
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


def _coerce_email_verified(raw: Any) -> bool:
    """Map the OIDC ``email_verified`` claim to a bool. IdPs vary: some
    emit a JSON bool, some emit the string ``"true"`` / ``"false"``.
    Anything we can't read as True maps to False."""
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    # Numbers, etc.
    try:
        return bool(int(raw))
    except (TypeError, ValueError):
        return False
