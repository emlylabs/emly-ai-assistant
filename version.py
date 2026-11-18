"""Single source of truth for the app's runtime version.

Resolution order, first non-empty wins:

1. ``APP_VERSION`` env var — set by the Dockerfile from a CI ``--build-arg``
   on tag pushes. This is the value to trust in deployed containers.
2. ``importlib.metadata.version("emly-ai-assistant")`` — populated by
   ``pyproject.toml`` for installed-package contexts (editable installs,
   non-Docker dev runs).
3. The project version in the adjacent ``pyproject.toml`` — used by source
   and Docker runs that do not install the project package.
4. ``"0.0.0-dev"`` — terminal fallback when no version is available.

``GIT_SHA`` and ``BUILT_AT`` carry the same Dockerfile/CI provenance; if
either env var is unset, callers see ``"unknown"``.
"""

from __future__ import annotations

import os
import tomllib
from importlib import metadata
from pathlib import Path


def _from_metadata() -> str | None:
    try:
        return metadata.version("emly-ai-assistant")
    except metadata.PackageNotFoundError:
        return None


def _from_pyproject() -> str | None:
    try:
        with Path(__file__).with_name("pyproject.toml").open("rb") as project_file:
            version = tomllib.load(project_file).get("project", {}).get("version")
        return version if isinstance(version, str) and version else None
    except (OSError, tomllib.TOMLDecodeError):
        return None


APP_VERSION: str = os.environ.get("APP_VERSION") or _from_metadata() or _from_pyproject() or "0.0.0-dev"
GIT_SHA: str = os.environ.get("GIT_SHA", "unknown")
BUILT_AT: str = os.environ.get("BUILT_AT", "unknown")


def info() -> dict[str, str]:
    """Dict shape returned by ``GET /api/version``."""
    return {"version": APP_VERSION, "git_sha": GIT_SHA, "built_at": BUILT_AT}
