"""Auth core abstractions: AuthProvider Protocol, Principal, TokenSet, exceptions.

The rest of the app interacts with auth only through ``AuthProvider`` and
``Principal``. Concrete providers implement the Protocol; swapping providers
does not require code changes elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class AuthError(Exception):
    """Base class for all auth-layer errors."""


class InvalidTokenError(AuthError):
    """Token is structurally invalid, has a bad signature, or fails claim validation."""


class ExpiredTokenError(InvalidTokenError):
    """Token's ``exp`` has passed (with leeway)."""


class InvalidIssuerError(InvalidTokenError):
    """Token's ``iss`` does not match the configured issuer."""


class InvalidAudienceError(InvalidTokenError):
    """Token's ``aud`` does not include any accepted audience."""


class MissingClaimError(InvalidTokenError):
    """Token is missing a required claim (``sub``, ``email``, …)."""


class UnknownProviderError(AuthError):
    """Provider config is missing or invalid."""


@dataclass(frozen=True)
class Principal:
    """A verified identity from an OIDC token."""

    issuer: str
    subject: str
    email: str
    email_verified: bool
    name: str | None = None
    raw_claims: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenSet:
    """The set of tokens returned by an OAuth2 token-endpoint exchange."""

    access_token: str
    id_token: str | None
    refresh_token: str | None
    expires_at: datetime


@dataclass(frozen=True)
class OAuthState:
    """Per-login transient state held server-side between authorize and callback.

    ``state`` defends the redirect against CSRF (round-tripped through the IdP).
    ``nonce`` defends the id_token against replay (embedded in id_token).
    ``code_verifier`` proves we initiated the dance (PKCE, RFC 7636).
    """

    state: str
    code_verifier: str
    nonce: str
    return_to: str | None
    created_at: datetime


class AuthProvider(Protocol):
    """Provider-portable auth interface. The only auth surface routes import."""

    def verify_token(self, token: str) -> Principal:
        """Validate a JWT and return its Principal. Raises ``InvalidTokenError`` on failure."""

    def authorize_url(self, oauth_state: OAuthState, redirect_uri: str) -> str:
        """Build the IdP authorize URL the browser should redirect to."""

    def exchange_code(
        self,
        code: str,
        oauth_state: OAuthState,
        redirect_uri: str,
    ) -> tuple[TokenSet, Principal]:
        """Exchange an authorization code for tokens and return the verified Principal.

        Implementations MUST validate the id_token's ``nonce`` against
        ``oauth_state.nonce`` before returning.
        """

    def logout_url(self, redirect_uri: str | None) -> str | None:
        """Return the IdP's end-session endpoint (or None if the IdP doesn't expose one)."""
