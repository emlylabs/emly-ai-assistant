"""Phase 1 unit tests for ``services/auth/oidc.py``.

Uses the in-process ``FakeOidcServer`` fixture — no docker, no real network.
Each test builds a fresh provider so the JWKS cache is isolated.
"""

from __future__ import annotations

import time

import pytest

from services.auth.base import (
    ExpiredTokenError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidTokenError,
    MissingClaimError,
    OAuthState,
)
from services.auth.oidc import (
    generate_nonce,
    generate_pkce_verifier,
    generate_state,
)
from tests.auth.fixtures.jwt_minter import (
    FakeOidcServer,
    jwks_dict,
    mint,
    mint_with_alien_key,
)

ISSUER = "https://idp.test.local"
AUDIENCE = "emly-admin-api"
CLIENT_ID = "emly-admin-console"


def _make_claims(**overrides):
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "email": "alice@example.com",
        "email_verified": True,
        "iat": now,
        "exp": now + 600,
    }
    base.update(overrides)
    return base


def _provider(**kwargs):
    fake = FakeOidcServer(ISSUER)
    p = fake.make_provider(**kwargs)
    return p, fake


def test_verify_valid_token_returns_principal():
    provider, _ = _provider()
    token = mint(_make_claims())
    principal = provider.verify_token(token)
    assert principal.subject == "user-123"
    assert principal.email == "alice@example.com"
    assert principal.email_verified is True
    assert principal.issuer == ISSUER


def test_verify_token_with_unknown_signing_key_rejected():
    provider, fake = _provider()
    bad_token, _alien_jwk = mint_with_alien_key()
    with pytest.raises(InvalidTokenError):
        provider.verify_token(bad_token)


def test_verify_token_with_bad_issuer_rejected():
    provider, _ = _provider()
    token = mint(_make_claims(iss="https://attacker.example.com"))
    with pytest.raises(InvalidIssuerError):
        provider.verify_token(token)


def test_verify_token_with_bad_audience_rejected():
    provider, _ = _provider()
    token = mint(_make_claims(aud="some-other-api"))
    with pytest.raises(InvalidAudienceError):
        provider.verify_token(token)


def test_verify_token_with_audience_fallback_to_client_id():
    """Cognito and friends emit `aud=client_id` rather than the configured audience."""
    provider, _ = _provider(audience_fallback_to_client_id=True)
    token = mint(_make_claims(aud=CLIENT_ID))
    principal = provider.verify_token(token)
    assert principal.subject == "user-123"


def test_verify_token_audience_fallback_off_rejects_client_id_aud():
    provider, _ = _provider(audience_fallback_to_client_id=False)
    token = mint(_make_claims(aud=CLIENT_ID))
    with pytest.raises(InvalidAudienceError):
        provider.verify_token(token)


def test_verify_expired_token_rejected():
    provider, _ = _provider()
    now = int(time.time())
    token = mint(_make_claims(exp=now - 600))
    with pytest.raises(ExpiredTokenError):
        provider.verify_token(token)


def test_verify_token_within_leeway_accepted():
    """Token exp 10s in the past is accepted because leeway=30."""
    provider, _ = _provider(leeway_seconds=30)
    now = int(time.time())
    token = mint(_make_claims(exp=now - 10))
    principal = provider.verify_token(token)
    assert principal.subject == "user-123"


def test_email_verified_always_true_when_idp_says_false():
    """Successful OIDC sign-in is treated as proof of email control, regardless
    of whatever the IdP emits in the ``email_verified`` claim."""
    provider, _ = _provider()
    token = mint(_make_claims(email_verified="false"))
    principal = provider.verify_token(token)
    assert principal.email_verified is True


def test_email_verified_always_true_when_claim_missing():
    provider, _ = _provider()
    claims = _make_claims()
    claims.pop("email_verified")
    token = mint(claims)
    principal = provider.verify_token(token)
    assert principal.email_verified is True


def test_token_missing_sub_rejected():
    provider, _ = _provider()
    claims = _make_claims()
    claims.pop("sub")
    token = mint(claims)
    with pytest.raises(MissingClaimError):
        provider.verify_token(token)


def test_token_missing_email_rejected():
    provider, _ = _provider()
    claims = _make_claims()
    claims.pop("email")
    token = mint(claims)
    with pytest.raises(MissingClaimError):
        provider.verify_token(token)


def test_authorize_url_includes_pkce_and_nonce_and_state():
    provider, _ = _provider()
    oauth_state = OAuthState(
        state="STATE-1",
        code_verifier=generate_pkce_verifier(),
        nonce="NONCE-1",
        return_to=None,
        created_at=__import__("datetime").datetime.now(),
    )
    url = provider.authorize_url(oauth_state, "https://app.test/callback")
    assert "state=STATE-1" in url
    assert "nonce=NONCE-1" in url
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "client_id=emly-admin-console" in url
    assert url.startswith(f"{ISSUER}/authorize?")


def test_exchange_code_round_trip_validates_nonce():
    provider, fake = _provider()
    nonce = generate_nonce()
    state = generate_state()
    verifier = generate_pkce_verifier()

    now = int(time.time())
    id_token = mint({
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "alice",
        "email": "alice@example.com",
        "email_verified": True,
        "iat": now,
        "exp": now + 600,
        "nonce": nonce,
    })
    fake.queue_token_response(access_token="ACCESS", id_token=id_token)

    oauth_state = OAuthState(
        state=state, code_verifier=verifier, nonce=nonce, return_to=None,
        created_at=__import__("datetime").datetime.now(),
    )
    tokenset, principal = provider.exchange_code("CODE", oauth_state, "https://app.test/callback")
    assert tokenset.access_token == "ACCESS"
    assert tokenset.id_token == id_token
    assert principal.email == "alice@example.com"


def test_exchange_code_rejects_id_token_with_wrong_nonce():
    provider, fake = _provider()
    expected_nonce = generate_nonce()
    wrong_nonce = generate_nonce()

    now = int(time.time())
    id_token = mint({
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "alice",
        "email": "alice@example.com",
        "email_verified": True,
        "iat": now,
        "exp": now + 600,
        "nonce": wrong_nonce,
    })
    fake.queue_token_response(access_token="ACCESS", id_token=id_token)

    oauth_state = OAuthState(
        state=generate_state(),
        code_verifier=generate_pkce_verifier(),
        nonce=expected_nonce,
        return_to=None,
        created_at=__import__("datetime").datetime.now(),
    )
    with pytest.raises(InvalidTokenError, match="nonce mismatch"):
        provider.exchange_code("CODE", oauth_state, "https://app.test/callback")


def test_logout_url_built_from_discovery():
    provider, _ = _provider()
    assert provider.logout_url(None) == f"{ISSUER}/logout"
    url = provider.logout_url("https://app.test/")
    assert url.startswith(f"{ISSUER}/logout?")
    assert "post_logout_redirect_uri=https" in url


def test_jwks_kid_miss_triggers_one_refresh_then_succeeds():
    """When a token has a kid not in our cache, we refresh JWKS once and retry."""
    fake = FakeOidcServer(ISSUER)
    provider = fake.make_provider()
    initial_jwks_calls = fake.jwks_calls

    new_jwk_priv, new_jwk_pub = _generate_alien_keypair_with_kid("rotated-kid")
    fake.set_jwks({"keys": [new_jwk_pub]})

    # Token signed by the new key. The provider doesn't have this kid yet.
    now = int(time.time())
    token = _mint_with_kid_and_priv(new_jwk_priv, "rotated-kid", _make_claims())

    principal = provider.verify_token(token)
    assert principal.subject == "user-123"
    # Exactly one extra JWKS fetch beyond the pre-warm.
    assert fake.jwks_calls == initial_jwks_calls + 1


def test_unknown_kid_after_refresh_still_fails():
    fake = FakeOidcServer(ISSUER)
    provider = fake.make_provider()
    bad_token, _ = mint_with_alien_key()
    with pytest.raises(InvalidTokenError, match="unknown signing key"):
        provider.verify_token(bad_token)


# -------- helpers used in JWKS rotation tests --------

def _generate_alien_keypair_with_kid(kid: str):
    """Returns (private_pem_bytes, public_jwk_dict) for a fresh keypair with the given kid."""
    from authlib.jose import JsonWebKey
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_jwk = JsonWebKey.import_key(pub_pem, {"kty": "RSA", "use": "sig", "kid": kid, "alg": "RS256"}).as_dict()
    return priv_pem, pub_jwk


def _mint_with_kid_and_priv(priv_pem: bytes, kid: str, claims: dict) -> str:
    from authlib.jose import JsonWebToken
    raw = JsonWebToken(["RS256"]).encode({"alg": "RS256", "kid": kid, "typ": "JWT"}, claims, priv_pem)
    return raw.decode("ascii") if isinstance(raw, bytes) else raw
