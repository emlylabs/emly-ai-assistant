"""Runtime-mode helpers shared by boot checks and security defaults.

Kept dependency-free (no ``config`` import) so it can be used from any
module, including ones imported before the embedding model loads.
"""

from __future__ import annotations

import os

# MODE values that switch production-safe defaults OFF. Production is the
# default (S8): unset, ``prod`` and anything unrecognised (a typo such as
# ``live``) all get the safe defaults, so only an explicit choice gets dev.
DEV_MODES = frozenset({"dev", "development", "local", "test"})
# Values that name a production-like deployment on purpose (no warning).
PROD_LIKE_MODES = frozenset({"prod", "production", "preprod", "stage", "staging"})


def current_mode() -> str:
    return (os.environ.get("MODE") or "").strip().lower()


def is_prod_mode() -> bool:
    """True unless ``MODE`` explicitly names a development mode."""
    return current_mode() not in DEV_MODES


def mode_warning() -> str | None:
    """A boot warning when production defaults apply by default rather than
    by choice: ``MODE`` unset or not a known value."""
    mode = current_mode()
    if mode in DEV_MODES or mode in PROD_LIKE_MODES:
        return None
    if not mode:
        return "MODE is not set: using production defaults. Set MODE=prod, or MODE=dev for local development."
    return (f"MODE={mode!r} is not a known value: using production defaults. "
            "Set MODE=prod, or MODE=dev for local development.")


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
