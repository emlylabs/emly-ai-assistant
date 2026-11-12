"""Runtime-mode helpers shared by boot checks and security defaults.

Kept dependency-free (no ``config`` import) so it can be used from any
module, including ones imported before the embedding model loads.
"""

from __future__ import annotations

import os

# MODE values that get production-safe defaults. Anything else (``dev``,
# ``local``, unset) is treated as local development.
PROD_LIKE_MODES = frozenset({"prod", "production", "preprod", "stage", "staging"})


def current_mode() -> str:
    return (os.environ.get("MODE") or "").strip().lower()


def is_prod_mode() -> bool:
    """True when ``MODE`` names a production-like deployment."""
    return current_mode() in PROD_LIKE_MODES


def env_flag(name: str, default: bool) -> bool:
    """Parse a boolean environment variable, falling back to ``default``
    when it is unset or empty."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Paths FastAPI serves for interactive docs / the schema. When docs are off
# these must 404 rather than fall through to the UI's SPA catch-all.
API_DOC_PATHS = frozenset({"docs", "docs/oauth2-redirect", "redoc", "openapi.json"})


def api_docs_enabled() -> bool:
    """Interactive API docs default to on in development and off in
    production-like modes; ``ENABLE_API_DOCS`` overrides either way."""
    return env_flag("ENABLE_API_DOCS", default=not is_prod_mode())


def fastapi_docs_kwargs() -> dict:
    """Keyword arguments for ``FastAPI()`` that switch the docs off when
    ``api_docs_enabled()`` is false."""
    if api_docs_enabled():
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}
