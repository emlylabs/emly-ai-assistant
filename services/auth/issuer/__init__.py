"""Embedded OIDC token service.

When ``AUTH_LOCAL_ISSUER_ENABLED=true`` (the default), the app mounts a set of
OIDC routes at ``/.well-known/*`` and ``/api/auth/local/*`` that respond as a
fully-fledged OIDC IdP — same OIDC discovery + JWKS + authorization-code flow
that Auth0/Clerk/Cognito implement, just hosted in-process.

The verifier (``services/auth/oidc.py``) talks to this service via the same code
path it uses for any external IdP. Whether ``AUTH_OIDC_ISSUER`` points at our
own ``${APP_BASE_URL}`` or at ``https://...auth0.com/`` is invisible to the
verifier.
"""
