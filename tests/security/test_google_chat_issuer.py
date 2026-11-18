"""S1: a Google-signed ID token is accepted only when Chat itself sent it.

With ``iss=https://accounts.google.com`` any Google account can mint a
token for an arbitrary audience (our webhook URL). Only tokens whose
``email`` is Chat's system service account, and verified, may pass.
"""

from __future__ import annotations

import asyncio
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from channels import jwt_verifier

AUD = "https://emly.example.com/api/channels/google_chat/webhook/abc"
KID = "g-key-1"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _google_keys(monkeypatch):
    for store in (jwt_verifier._jwks_cache, jwt_verifier._jwks_failures,
                  jwt_verifier._jwks_kid_refresh_attempts):
        store.clear()

    async def fake_jwks(url):
        assert url == jwt_verifier.GOOGLE_OIDC_JWKS_URI
        return {KID: _KEY.public_key()}

    monkeypatch.setattr(jwt_verifier, "_fetch_jwks", fake_jwks)
    yield
    for store in (jwt_verifier._jwks_cache, jwt_verifier._jwks_failures,
                  jwt_verifier._jwks_kid_refresh_attempts):
        store.clear()


def _token(**claims) -> str:
    now = int(time.time())
    body = {"iss": jwt_verifier.GOOGLE_OIDC_ISSUER, "aud": AUD, "iat": now, "exp": now + 300}
    body.update(claims)
    return pyjwt.encode(body, _KEY, algorithm="RS256", headers={"kid": KID})


def _verify(token: str):
    return asyncio.run(jwt_verifier.verify_google_chat_jwt(
        token, audience=AUD,
        allowed_issuers=[jwt_verifier.GOOGLE_SYSTEM_SA, jwt_verifier.GOOGLE_OIDC_ISSUER]))


def test_chat_service_account_token_accepted():
    claims = _verify(_token(email=jwt_verifier.GOOGLE_SYSTEM_SA, email_verified=True))
    assert claims["email"] == jwt_verifier.GOOGLE_SYSTEM_SA


@pytest.mark.parametrize("extra", [
    {},                                                         # no email at all
    {"email": "attacker@evil-project.iam.gserviceaccount.com", "email_verified": True},
    {"email": "someone@gmail.com", "email_verified": True},
    {"email": jwt_verifier.GOOGLE_SYSTEM_SA},                   # email_verified missing
    {"email": jwt_verifier.GOOGLE_SYSTEM_SA, "email_verified": False},
    {"email": jwt_verifier.GOOGLE_SYSTEM_SA, "email_verified": "true"},
])
def test_other_google_accounts_refused(extra):
    with pytest.raises(PermissionError):
        _verify(_token(**extra))
