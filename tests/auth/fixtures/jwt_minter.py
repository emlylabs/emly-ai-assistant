"""In-process JWT minter + ``FakeOidcServer`` for unit tests.

Generates an RS256 keypair at module import. Exposes:

- ``mint(claims)`` — sign claims and return a compact JWT string.
- ``jwks_dict()`` — public JWKS dict.
- ``discovery_dict(issuer)`` — minimal OIDC discovery doc.
- ``FakeOidcServer`` — ``httpx.MockTransport`` handler that responds at
  ``{issuer}/.well-known/...`` and ``{issuer}/token``. Pass its client into
  ``OidcProvider(http_client=fake.client())`` to verify against this fake.
"""

from __future__ import annotations

from typing import Any

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from services.auth.oidc import OidcProvider


_KID = "test-key-1"


def _generate_keypair() -> tuple[bytes, dict[str, Any]]:
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
    pub_jwk = JsonWebKey.import_key(
        pub_pem, {"kty": "RSA", "use": "sig", "kid": _KID, "alg": "RS256"}
    ).as_dict()
    return priv_pem, pub_jwk


_PRIV_PEM, _PUB_JWK = _generate_keypair()


def jwks_dict() -> dict[str, Any]:
    return {"keys": [_PUB_JWK]}


def discovery_dict(issuer: str) -> dict[str, Any]:
    issuer = issuer.rstrip("/")
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "end_session_endpoint": f"{issuer}/logout",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


def mint(claims: dict[str, Any], *, kid: str = _KID) -> str:
    """Sign a JWT with the test keypair. Caller supplies all claims explicitly."""
    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    raw = JsonWebToken(["RS256"]).encode(header, claims, _PRIV_PEM)
    return raw.decode("ascii") if isinstance(raw, bytes) else raw


def mint_with_alien_key() -> tuple[str, dict[str, Any]]:
    """Mint a token signed by a *different* keypair, with a JWKS that doesn't include it.

    Used to verify that a token signed by an unknown key is rejected.
    """
    priv_pem, _pub_jwk = _generate_keypair()
    header = {"alg": "RS256", "kid": "alien-key", "typ": "JWT"}
    raw = JsonWebToken(["RS256"]).encode(header, {"sub": "x"}, priv_pem)
    token = raw.decode("ascii") if isinstance(raw, bytes) else raw
    return token, _pub_jwk


class FakeOidcServer:
    """``httpx.MockTransport`` handler that pretends to be an OIDC IdP."""

    def __init__(self, issuer: str) -> None:
        self.issuer = issuer.rstrip("/")
        self._token_responses: list[dict[str, Any]] = []
        self._jwks_override: dict[str, Any] | None = None
        self.discovery_calls = 0
        self.jwks_calls = 0
        self.token_calls = 0

    def set_jwks(self, jwks: dict[str, Any]) -> None:
        """Override the JWKS served. Used to simulate key rotation."""
        self._jwks_override = jwks

    def queue_token_response(
        self,
        *,
        access_token: str,
        id_token: str,
        refresh_token: str | None = None,
        expires_in: int = 3600,
    ) -> None:
        self._token_responses.append(
            {
                "access_token": access_token,
                "id_token": id_token,
                "refresh_token": refresh_token,
                "expires_in": expires_in,
                "token_type": "Bearer",
            }
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{self.issuer}/.well-known/openid-configuration":
            self.discovery_calls += 1
            return httpx.Response(200, json=discovery_dict(self.issuer))
        if url == f"{self.issuer}/.well-known/jwks.json":
            self.jwks_calls += 1
            return httpx.Response(200, json=self._jwks_override or jwks_dict())
        if url == f"{self.issuer}/token":
            self.token_calls += 1
            if not self._token_responses:
                return httpx.Response(500, json={"error": "no_canned_response"})
            return httpx.Response(200, json=self._token_responses.pop(0))
        return httpx.Response(404, json={"error": "fake_idp_unknown_url", "url": url})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def make_provider(
        self,
        *,
        audience: str = "emly-admin-api",
        client_id: str = "emly-admin-console",
        client_secret: str | None = None,
        leeway_seconds: int = 30,
        audience_fallback_to_client_id: bool = False,
    ) -> OidcProvider:
        return OidcProvider(
            issuer=self.issuer,
            audience=audience,
            client_id=client_id,
            client_secret=client_secret,
            leeway_seconds=leeway_seconds,
            audience_fallback_to_client_id=audience_fallback_to_client_id,
            http_client=self.client(),
        )
