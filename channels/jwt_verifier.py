"""Inbound JWT verification for Teams + Google Chat.

Both platforms authenticate webhook delivery by sending a signed JWT
in the ``Authorization: Bearer …`` header. We:

1. Fetch the JWKS / x509 cert set once per issuer, cache for 24h.
2. Decode the JWT header (unverified) to pick the matching kid.
3. Verify signature + iss + aud + exp using PyJWT.

The cache is process-local, so during a key-rotation window two
workers may briefly disagree — acceptable: we cache aggressively and
the rotated keys converge within hours.

Fail-closed: a JWKS fetch failure rejects the inbound. An attacker who
can interrupt the JWKS endpoint can DoS our channel inbound, which is
acceptable; the alternative (fail-open) lets the same attacker bypass
authentication.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import jwt as pyjwt
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from channels.auth._http import make_client

log = logging.getLogger(__name__)

JWKS_CACHE_TTL = 24 * 3600
# Cool-down after a JWKS fetch failure. Without it, every inbound
# webhook during a JWKS-endpoint outage tries to refetch, hammering the
# already-broken endpoint. With it, we serve stale-but-known-good keys
# (or fail fast if we never had any) for `JWKS_FAILURE_COOLDOWN`
# seconds before retrying.
JWKS_FAILURE_COOLDOWN = 30
# Minimum interval between forced refreshes triggered by a kid-miss.
# Google rotates signing keys more often than the 24h cache TTL, so a
# brand-new kid arrives mid-TTL and our cached set looks stale. We
# refetch on demand, but rate-limit it: an attacker spraying random kids
# would otherwise trigger one upstream call per request.
JWKS_KID_REFRESH_MIN_INTERVAL = 60


@dataclass
class _CachedKeys:
    keys: Dict[str, str]  # kid -> PEM-encoded public key
    fetched_at: float


_jwks_cache: Dict[str, _CachedKeys] = {}
_jwks_failures: Dict[str, float] = {}
_jwks_kid_refresh_attempts: Dict[str, float] = {}
_jwks_lock = asyncio.Lock()


async def _fetch_jwks(jwks_uri: str) -> Dict[str, str]:
    async with make_client() as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        body = resp.json()
    out: Dict[str, str] = {}
    for k in body.get("keys", []):
        kid = k.get("kid")
        if not kid:
            continue
        try:
            pem = pyjwt.algorithms.RSAAlgorithm.from_jwk(k)
            out[kid] = pem.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")
        except Exception:
            log.exception("Failed to convert JWK kid=%s", kid)
    return out


async def _fetch_x509_pem_set(cert_url: str) -> Dict[str, str]:
    """Google Chat exposes a ``{kid: x509_pem}`` JSON map (not JWKS)."""
    async with make_client() as client:
        resp = await client.get(cert_url)
        resp.raise_for_status()
        body = resp.json()
    out: Dict[str, str] = {}
    for kid, cert_pem in body.items():
        try:
            cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"), default_backend())
            pub = cert.public_key()
            out[kid] = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")
        except Exception:
            log.exception("Failed to parse x509 cert kid=%s", kid)
    return out


async def _get_keys(cache_key: str, fetcher) -> Dict[str, str]:
    cached = _jwks_cache.get(cache_key)
    now = time.time()
    if cached and (now - cached.fetched_at) < JWKS_CACHE_TTL:
        return cached.keys

    last_failure = _jwks_failures.get(cache_key, 0.0)
    if now - last_failure < JWKS_FAILURE_COOLDOWN:
        # Recent fetch failed and the cooldown hasn't elapsed. Serve
        # stale keys if we have them — far better than rejecting every
        # inbound during a transient JWKS outage. If we've never had
        # keys, raise a clean PermissionError so the verify path fails
        # closed.
        if cached:
            log.warning(
                "JWKS fetch in cooldown for %s; serving stale keys (age=%.0fs)",
                cache_key, now - cached.fetched_at,
            )
            return cached.keys
        raise PermissionError(f"JWKS fetch failed recently for {cache_key}")

    async with _jwks_lock:
        cached = _jwks_cache.get(cache_key)
        if cached and (time.time() - cached.fetched_at) < JWKS_CACHE_TTL:
            return cached.keys
        try:
            keys = await fetcher()
        except Exception:
            _jwks_failures[cache_key] = time.time()
            log.exception("JWKS fetch failed for %s; %ds cooldown engaged", cache_key, JWKS_FAILURE_COOLDOWN)
            if cached:
                return cached.keys  # serve stale rather than reject everything
            raise
        _jwks_cache[cache_key] = _CachedKeys(keys=keys, fetched_at=time.time())
        _jwks_failures.pop(cache_key, None)
        return keys


async def _force_refresh_keys(cache_key: str, fetcher) -> Dict[str, str]:
    """Bypass the TTL and refetch — used when a kid arrived that isn't in
    the cached set (i.e. an upstream rotation happened mid-TTL). Rate
    limited per cache_key to bound the impact of a kid-spray attack.
    """
    now = time.time()
    last_attempt = _jwks_kid_refresh_attempts.get(cache_key, 0.0)
    cached = _jwks_cache.get(cache_key)
    if now - last_attempt < JWKS_KID_REFRESH_MIN_INTERVAL:
        return cached.keys if cached else {}
    async with _jwks_lock:
        last_attempt = _jwks_kid_refresh_attempts.get(cache_key, 0.0)
        if time.time() - last_attempt < JWKS_KID_REFRESH_MIN_INTERVAL:
            cached = _jwks_cache.get(cache_key)
            return cached.keys if cached else {}
        _jwks_kid_refresh_attempts[cache_key] = time.time()
        try:
            keys = await fetcher()
        except Exception:
            _jwks_failures[cache_key] = time.time()
            log.exception("JWKS forced refresh failed for %s", cache_key)
            cached = _jwks_cache.get(cache_key)
            return cached.keys if cached else {}
        _jwks_cache[cache_key] = _CachedKeys(keys=keys, fetched_at=time.time())
        _jwks_failures.pop(cache_key, None)
        log.info("JWKS forced refresh for %s — %d keys loaded", cache_key, len(keys))
        return keys


# ---------------------------------------------------------------------------
# Bot Framework (Teams) JWKS
# ---------------------------------------------------------------------------
BOT_FRAMEWORK_OPENID = "https://login.botframework.com/v1/.well-known/openidconfiguration"
BOT_FRAMEWORK_ISSUER = "https://api.botframework.com"


async def verify_bot_framework_jwt(token: str, audience: str) -> dict:
    """Verify a Bot Framework JWT and return the claims dict."""
    async def fetcher() -> Dict[str, str]:
        async with make_client() as client:
            resp = await client.get(BOT_FRAMEWORK_OPENID)
            resp.raise_for_status()
            cfg = resp.json()
        return await _fetch_jwks(cfg["jwks_uri"])

    return await _verify(
        token,
        cache_key="bot_framework",
        fetcher=fetcher,
        issuer=BOT_FRAMEWORK_ISSUER,
        audience=audience,
    )


# ---------------------------------------------------------------------------
# Google Chat
# ---------------------------------------------------------------------------
# Google Chat signs inbound JWTs with one of three issuers, depending on the
# Chat app's auth-audience setting:
#  1. ``chat@system.gserviceaccount.com`` — the Chat system service account,
#  2. The app's own service account email,
#  3. ``https://accounts.google.com`` — Google's standard OIDC issuer.
# We accept any caller-allowed value, pick the JWKS source from the
# (unverified) ``iss`` claim, and then verify signature/iss/aud strictly.
GOOGLE_SYSTEM_SA = "chat@system.gserviceaccount.com"
GOOGLE_OIDC_ISSUER = "https://accounts.google.com"
GOOGLE_OIDC_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_X509_URL_TEMPLATE = (
    "https://www.googleapis.com/service_accounts/v1/metadata/x509/{email}"
)
# Defensive guard on the email we splice into the cert URL — Google SA emails
# follow this shape, anything else is a misconfiguration we shouldn't fetch.
_SA_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.gserviceaccount\.com$")


async def verify_google_chat_jwt(
    token: str, audience: str, allowed_issuers: Iterable[str]
) -> dict:
    allowed = {i for i in allowed_issuers if i}
    if not allowed:
        raise PermissionError("no allowed issuers configured")
    try:
        unverified = pyjwt.decode(token, options={"verify_signature": False})
    except pyjwt.PyJWTError as e:
        raise PermissionError(f"malformed jwt: {e}")
    iss = unverified.get("iss")
    if not iss or iss not in allowed:
        raise PermissionError(f"unexpected issuer {iss!r}; allowed={sorted(allowed)}")

    if iss == GOOGLE_OIDC_ISSUER:
        async def fetcher() -> Dict[str, str]:
            return await _fetch_jwks(GOOGLE_OIDC_JWKS_URI)

        cache_key = "google_oidc"
    else:
        if not _SA_EMAIL_RE.match(iss):
            raise PermissionError(f"issuer not a service-account email: {iss!r}")
        cert_url = _GOOGLE_X509_URL_TEMPLATE.format(email=iss)

        async def fetcher() -> Dict[str, str]:
            return await _fetch_x509_pem_set(cert_url)

        cache_key = f"google_chat:{iss}"

    return await _verify(
        token,
        cache_key=cache_key,
        fetcher=fetcher,
        issuer=iss,
        audience=audience,
    )


async def _verify(token: str, cache_key: str, fetcher, issuer: str, audience: str) -> dict:
    try:
        unverified_header = pyjwt.get_unverified_header(token)
    except pyjwt.PyJWTError as e:
        raise PermissionError(f"malformed jwt header: {e}")
    kid = unverified_header.get("kid")
    if not kid:
        raise PermissionError("missing kid in jwt header")
    keys = await _get_keys(cache_key, fetcher)
    if kid not in keys:
        # Upstream may have rotated keys mid-TTL — try one rate-limited
        # refresh before rejecting. Strict kid match either way.
        keys = await _force_refresh_keys(cache_key, fetcher)
        if kid not in keys:
            raise PermissionError(f"unknown signing key id: {kid}")
    try:
        return pyjwt.decode(
            token,
            keys[kid],
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except pyjwt.PyJWTError as e:
        raise PermissionError(f"jwt verification failed: {e}")


def extract_unverified_audience(token: str) -> Optional[str]:
    """Peek at the JWT's ``aud`` without verifying — used to look up the
    matching ``BotChannel`` row before we know which secrets to verify
    against. Caller MUST verify before trusting the token."""
    try:
        claims = pyjwt.decode(token, options={"verify_signature": False})
    except pyjwt.PyJWTError:
        return None
    aud = claims.get("aud")
    if isinstance(aud, list):
        return aud[0] if aud else None
    return aud
