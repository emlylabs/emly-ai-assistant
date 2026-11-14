"""F1: forged-token header tricks are rejected by both JWT verifiers.

Covers the attack classes behind the Authlib and PyJWT advisories fixed by
the F1 upgrade: a signing key embedded in the token header (``jwk``/``jku``),
``alg: none``, HS256 signed with the public key (algorithm confusion) and an
unknown ``crit`` extension (CVE-2026-32597, accepted by PyJWT < 2.12).

Tokens are assembled by hand so no library helper can quietly fix them up.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from channels import jwt_verifier
from services.auth.base import InvalidTokenError
from tests.auth.fixtures import jwt_minter
from tests.auth.fixtures.jwt_minter import FakeOidcServer

ISSUER = "https://idp.test.local"
AUDIENCE = "emly-admin-api"
KID = "test-key-1"  # the kid the fake IdP publishes


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_b64(n: int) -> str:
    return _b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def _segments(header: dict, claims: dict) -> str:
    return _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(claims).encode())


def _rs256(header: dict, claims: dict, private_key) -> str:
    signing_input = _segments(header, claims)
    sig = private_key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64(sig)


def _hs256(header: dict, claims: dict, secret: bytes) -> str:
    signing_input = _segments(header, claims)
    return signing_input + "." + _b64(hmac.new(secret, signing_input.encode(), hashlib.sha256).digest())


def _unsigned(header: dict, claims: dict) -> str:
    return _segments(header, claims) + "."


def _claims(**extra) -> dict:
    now = int(time.time())
    return {"iss": ISSUER, "aud": AUDIENCE, "sub": "attacker", "email": "attacker@example.com",
            "email_verified": True, "iat": now, "exp": now + 600, **extra}


ATTACKER = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ATTACKER_JWK = {"kty": "RSA", "kid": KID, "use": "sig", "alg": "RS256",
                "n": _int_b64(ATTACKER.public_key().public_numbers().n),
                "e": _int_b64(ATTACKER.public_key().public_numbers().e)}


def _victim_public_pem() -> bytes:
    """The IdP's public key: anyone can read it from the JWKS."""
    priv = serialization.load_pem_private_key(jwt_minter._PRIV_PEM, password=None)
    return priv.public_key().public_bytes(serialization.Encoding.PEM,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)


def _forged_tokens(claims: dict) -> dict:
    return {
        "embedded jwk, our kid": _rs256({"alg": "RS256", "kid": KID, "jwk": ATTACKER_JWK}, claims, ATTACKER),
        "embedded jwk, no kid": _rs256({"alg": "RS256", "jwk": ATTACKER_JWK}, claims, ATTACKER),
        "jku to attacker": _rs256({"alg": "RS256", "kid": KID, "jku": "https://attacker.example/jwks.json"},
                                  claims, ATTACKER),
        "alg none": _unsigned({"alg": "none", "kid": KID}, claims),
        "HS256 with public key": _hs256({"alg": "HS256", "kid": KID}, claims, _victim_public_pem()),
    }


# ------------------------------------------------------------ admin sign-in


@pytest.fixture
def oidc():
    fake = FakeOidcServer(ISSUER)
    requested: list = []
    handler = fake.handler

    def recording(request):
        requested.append(str(request.url))
        return handler(request)

    fake.handler = recording
    return fake.make_provider(), requested


def test_oidc_sanity_genuine_token_accepted(oidc):
    provider, _ = oidc
    assert provider.verify_token(jwt_minter.mint(_claims())).subject == "attacker"


@pytest.mark.parametrize("name", list(_forged_tokens(_claims())))
def test_oidc_rejects_forged_header(oidc, name):
    provider, requested = oidc
    token = _forged_tokens(_claims())[name]
    with pytest.raises(InvalidTokenError):
        provider.verify_token(token)
    assert not any("attacker.example" in u for u in requested)


# ------------------------------------------------- channel webhooks (PyJWT)


def _verify_channel(token: str, cache_key: str):
    async def fetcher():
        return {KID: _victim_public_pem().decode("ascii")}

    return asyncio.run(jwt_verifier._verify(token, cache_key=cache_key, fetcher=fetcher,
                                            issuer=ISSUER, audience=AUDIENCE))


@pytest.fixture(autouse=True)
def _clean_channel_cache():
    yield
    for store in (jwt_verifier._jwks_cache, jwt_verifier._jwks_failures,
                  jwt_verifier._jwks_kid_refresh_attempts):
        for key in [k for k in store if k.startswith("f1-test")]:
            store.pop(key)


def _victim_signed(header: dict, claims: dict) -> str:
    victim = serialization.load_pem_private_key(jwt_minter._PRIV_PEM, password=None)
    return _rs256(header, claims, victim)


def test_channel_sanity_genuine_token_accepted():
    claims = _verify_channel(_victim_signed({"alg": "RS256", "kid": KID}, _claims()), "f1-test-ok")
    assert claims["sub"] == "attacker"


@pytest.mark.parametrize("name", list(_forged_tokens(_claims())))
def test_channel_rejects_forged_header(name):
    with pytest.raises(PermissionError):
        _verify_channel(_forged_tokens(_claims())[name], f"f1-test-{name}")


def test_channel_rejects_unknown_crit_extension():
    # Correctly signed by the real key, but marks an extension we don't
    # understand as critical: RFC 7515 requires rejection.
    token = _victim_signed({"alg": "RS256", "kid": KID, "crit": ["urn:example:must-understand"],
                            "urn:example:must-understand": True}, _claims())
    with pytest.raises(PermissionError):
        _verify_channel(token, "f1-test-crit")
