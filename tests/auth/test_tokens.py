"""Tests for ``services/auth/issuer/tokens.py``.

The end-to-end test is the load-bearing one: a token minted by the embedded
issuer's keystore must verify cleanly through ``OidcProvider`` when the
provider is pointed at a fake discovery doc serving the same JWKS. That's
the proof that the embedded service is OIDC-spec-compliant — same code path
the verifier uses against any external IdP.
"""

from __future__ import annotations

import base64
import json

import httpx

from services.auth.issuer.keystore import Keystore
from services.auth.issuer.tokens import mint_access_token, mint_id_token
from services.auth.oidc import OidcProvider

ISSUER = "https://emly-embedded.test"
AUDIENCE = "emly-admin-api"
CLIENT_ID = "emly-admin-console"


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _decode_unverified(token: str) -> tuple[dict, dict]:
    """Pull header + claims out of a JWT without checking signature (for assertions only)."""
    h_b64, c_b64, _sig = token.split(".")
    return json.loads(_b64url_decode(h_b64)), json.loads(_b64url_decode(c_b64))


def _provider_against_keystore(keystore: Keystore, **kwargs) -> OidcProvider:
    """Build an OidcProvider whose JWKS comes from the supplied keystore (via mock transport)."""
    discovery_doc = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=discovery_doc)
        if url.endswith("/.well-known/jwks.json"):
            return httpx.Response(200, json=keystore.jwks_dict())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OidcProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        client_id=CLIENT_ID,
        http_client=client,
        **kwargs,
    )


def test_access_token_verifies_through_oidc_provider(tmp_path):
    ks = Keystore(tmp_path)
    token = mint_access_token(
        keystore=ks,
        issuer=ISSUER,
        audience=AUDIENCE,
        subject="adm-1",
        email="alice@example.com",
        email_verified=True,
        ttl_seconds=3600,
    )
    provider = _provider_against_keystore(ks)
    principal = provider.verify_token(token)
    assert principal.subject == "adm-1"
    assert principal.email == "alice@example.com"
    assert principal.email_verified is True


def test_id_token_includes_nonce_and_verifies(tmp_path):
    ks = Keystore(tmp_path)
    token = mint_id_token(
        keystore=ks,
        issuer=ISSUER,
        audience=AUDIENCE,
        subject="adm-1",
        email="alice@example.com",
        email_verified=True,
        nonce="abc123",
        ttl_seconds=3600,
        name="Alice",
    )
    provider = _provider_against_keystore(ks)
    principal = provider.verify_token(token)
    assert principal.raw_claims["nonce"] == "abc123"
    assert principal.name == "Alice"


def test_token_header_carries_kid_matching_current_keystore(tmp_path):
    ks = Keystore(tmp_path)
    token = mint_access_token(
        keystore=ks, issuer=ISSUER, audience=AUDIENCE,
        subject="adm-1", email="x@y.z", email_verified=True, ttl_seconds=60,
    )
    header, _claims = _decode_unverified(token)
    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"
    assert header["kid"] == ks.current_kid()


def test_after_rotate_new_tokens_are_signed_by_new_key_old_remain_valid(tmp_path):
    ks = Keystore(tmp_path)
    old_token = mint_access_token(
        keystore=ks, issuer=ISSUER, audience=AUDIENCE,
        subject="adm-1", email="x@y.z", email_verified=True, ttl_seconds=3600,
    )
    old_kid = ks.current_kid()

    new_kid = ks.rotate()
    new_token = mint_access_token(
        keystore=ks, issuer=ISSUER, audience=AUDIENCE,
        subject="adm-2", email="b@y.z", email_verified=True, ttl_seconds=3600,
    )

    new_header, _ = _decode_unverified(new_token)
    assert new_header["kid"] == new_kid
    assert new_kid != old_kid

    provider = _provider_against_keystore(ks)
    # Both tokens verify because JWKS still includes the old public key.
    assert provider.verify_token(old_token).subject == "adm-1"
    assert provider.verify_token(new_token).subject == "adm-2"


def test_jti_unique_per_token(tmp_path):
    ks = Keystore(tmp_path)
    t1 = mint_access_token(
        keystore=ks, issuer=ISSUER, audience=AUDIENCE,
        subject="x", email="x@y.z", email_verified=True, ttl_seconds=60,
    )
    t2 = mint_access_token(
        keystore=ks, issuer=ISSUER, audience=AUDIENCE,
        subject="x", email="x@y.z", email_verified=True, ttl_seconds=60,
    )
    _, c1 = _decode_unverified(t1)
    _, c2 = _decode_unverified(t2)
    assert c1["jti"] != c2["jti"]


def test_extra_claims_merged_into_access_token(tmp_path):
    ks = Keystore(tmp_path)
    token = mint_access_token(
        keystore=ks, issuer=ISSUER, audience=AUDIENCE,
        subject="x", email="x@y.z", email_verified=True, ttl_seconds=60,
        extra={"custom": "value", "scope": "admin"},
    )
    _, claims = _decode_unverified(token)
    assert claims["custom"] == "value"
    assert claims["scope"] == "admin"
