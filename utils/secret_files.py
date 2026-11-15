"""G6c: read secrets from files (``NAME_FILE``), for Docker and Kubernetes
secrets.

For each secret below, ``NAME_FILE=/run/secrets/name`` makes the app read
``NAME`` from that file instead of the environment, so the value isn't
in ``docker inspect``, the pod spec or a process listing. A trailing
newline is removed; nothing else is.

- Setting both ``NAME`` and ``NAME_FILE`` (to different values) stops
  start-up: which one wins would be a guess.
- A missing, unreadable or empty file stops start-up.
- Only the variable names are logged, never the values or file contents.

``load_secret_files()`` runs at the top of ``main`` and ``config``
before anything reads these variables, and is safe to call twice.
Kept dependency-free so it can run before anything else is imported.
"""
from __future__ import annotations

import logging
import os
from typing import List

log = logging.getLogger(__name__)

SECRET_NAMES = (
    "BOT_SECRETS_KEY",
    "BOT_SECRETS_KEY_PREVIOUS",
    "OPENAI_API_KEY",
    "DATABASE_URL",
    "QDRANT_API_KEY",
    "EMAIL_PASSWORD",
    "AUTH_OIDC_CLIENT_SECRET",
    "AUTH_LOCAL_BOOTSTRAP_PASSWORD",
    "META_APP_SECRET",
    "META_CLIENT_SECRET",
)


class SecretFileError(RuntimeError):
    """A ``NAME_FILE`` setting can't be used."""


def load_secret_files() -> List[str]:
    """Set each ``NAME`` from ``NAME_FILE``. Returns the names read."""
    loaded = []
    for name in SECRET_NAMES:
        path = (os.environ.get(f"{name}_FILE") or "").strip()
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                value = fh.read().rstrip("\r\n")
        except OSError as exc:
            raise SecretFileError(f"{name}_FILE: can't read the file ({type(exc).__name__})") from None
        except UnicodeDecodeError:
            raise SecretFileError(f"{name}_FILE: the file isn't UTF-8 text") from None
        if not value.strip():
            raise SecretFileError(f"{name}_FILE: the file is empty")
        current = os.environ.get(name)
        if current and current != value:
            raise SecretFileError(f"Both {name} and {name}_FILE are set. Set only one.")
        if current != value:
            os.environ[name] = value
            loaded.append(name)
    if loaded:
        log.info("Read from files: %s", ", ".join(loaded))
    return loaded
