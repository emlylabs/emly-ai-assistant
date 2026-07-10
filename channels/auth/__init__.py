"""Channel authentication strategies.

Each adapter pairs an `AuthStrategy` with the per-platform inbound /
outbound logic. Strategies own credential acquisition, refresh, and
optional revoke; adapters call ``auth.get_access_token(channel)`` rather
than reading the secret blob directly so token rotation is invisible to
adapter code.
"""
from channels.auth.base import AuthStrategy, InstallMetadata
from channels.auth.oauth2_auth_code import OAuth2AuthCodeBase, TokenSet
from channels.auth.oauth2_client_credentials import OAuth2ClientCredentials
from channels.auth.service_account_jwt import ServiceAccountJWT
from channels.auth.static_token import StaticToken

__all__ = [
    "AuthStrategy",
    "InstallMetadata",
    "OAuth2AuthCodeBase",
    "OAuth2ClientCredentials",
    "ServiceAccountJWT",
    "StaticToken",
    "TokenSet",
]
