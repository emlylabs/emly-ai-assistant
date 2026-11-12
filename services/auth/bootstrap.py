"""First-boot superadmin provisioning.

Pre-stages the first admin in ``pending_admins`` from
``AUTH_BOOTSTRAP_SUPERADMIN_EMAIL`` and, when the embedded issuer is on,
seeds a ``bootstrap:<email>`` local credential.

The generated temporary password is **never logged**. It is written once
to ``{DATA_DIR}/bootstrap_password.txt`` (mode 0600) and only that path is
logged. The file is removed when the bootstrap credential is consumed on
first sign-in (see ``remove_bootstrap_password_file``).
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

BOOTSTRAP_PASSWORD_FILENAME = "bootstrap_password.txt"


def _data_dir() -> Path:
    from config import DATA_DIR

    return Path(DATA_DIR)


def bootstrap_password_path(data_dir: Optional[Path] = None) -> Path:
    return (data_dir or _data_dir()) / BOOTSTRAP_PASSWORD_FILENAME


def _write_password_file(path: Path, email: str, password: str) -> None:
    """Write the temporary password with owner-only permissions.

    ``os.open`` with an explicit mode avoids a window where the file exists
    with umask-default permissions; the ``chmod`` covers a pre-existing file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(
            fd,
            (
                f"email: {email}\n"
                f"temporary_password: {password}\n"
                "This password must be changed on first sign-in. "
                "This file is deleted automatically after the first successful sign-in.\n"
            ).encode("utf-8"),
        )
    finally:
        os.close(fd)
    os.chmod(str(path), 0o600)


def remove_bootstrap_password_file(data_dir: Optional[Path] = None) -> None:
    """Delete the bootstrap password file if present. Never raises."""
    try:
        path = bootstrap_password_path(data_dir)
        if path.exists():
            path.unlink()
            log.info("[BOOTSTRAP] removed %s after first sign-in", path)
    except Exception:  # noqa: BLE001 — best-effort cleanup
        log.warning("[BOOTSTRAP] could not remove bootstrap password file", exc_info=True)


def bootstrap_pending_superadmin(data_dir: Optional[Path] = None) -> None:
    """Idempotent — runs on every boot. Behavior:

      - No admin yet, no pending row → create both, seed credential.
      - Pending row exists, ``AUTH_LOCAL_BOOTSTRAP_PASSWORD`` set →
        reconcile the credential to match the env (operator's env wins).
      - Pending row exists, env password unset → leave the existing
        credential alone (don't churn the random one).
      - Admin user exists (post-activation) → do nothing; password reset
        goes through the proper change-password flow.
    """
    from models.admin_users import AdminUsers

    bootstrap_email = os.environ.get("AUTH_BOOTSTRAP_SUPERADMIN_EMAIL", "").strip().lower()
    if not bootstrap_email:
        return

    if AdminUsers.get_by_email(bootstrap_email) is not None:
        # Already activated — env-based password reset isn't supported here.
        return

    from models.pending_admins import PendingAdmins
    from services.auth.issuer.factory import is_local_issuer_enabled

    if PendingAdmins.get_active_by_email(bootstrap_email) is None:
        PendingAdmins.create(
            email=bootstrap_email,
            invited_by=None,
            is_superadmin=True,
            bot_assignments=[],
        )

    if not is_local_issuer_enabled():
        return

    from models.local_credentials import LocalCredentials
    from services.auth.issuer.passwords import hash_password

    sentinel_id = f"bootstrap:{bootstrap_email}"
    env_password = os.environ.get("AUTH_LOCAL_BOOTSTRAP_PASSWORD", "").strip()
    existing_credential = LocalCredentials.get(sentinel_id)

    if existing_credential is None:
        # First-time provisioning: use env password if set, else generate.
        password = env_password or secrets.token_urlsafe(18)
        LocalCredentials.upsert(
            admin_id=sentinel_id,
            password_hash=hash_password(password),
            must_change=True,
        )
        if env_password:
            log.warning(
                "[BOOTSTRAP] superadmin %s — credential seeded from "
                "AUTH_LOCAL_BOOTSTRAP_PASSWORD env var",
                bootstrap_email,
            )
        else:
            path = bootstrap_password_path(data_dir)
            _write_password_file(path, bootstrap_email, password)
            log.warning(
                "[BOOTSTRAP] superadmin %s — temporary password written to %s "
                "(mode 0600; must be changed on first login; set "
                "AUTH_LOCAL_BOOTSTRAP_PASSWORD to use a known value instead)",
                bootstrap_email,
                path,
            )
    elif env_password:
        # Reconcile to env — the operator changed/added AUTH_LOCAL_BOOTSTRAP_PASSWORD
        # since the last boot, and the pending admin is still un-activated.
        LocalCredentials.upsert(
            admin_id=sentinel_id,
            password_hash=hash_password(env_password),
            must_change=True,
        )
        # A stale generated password no longer works; don't leave it lying around.
        remove_bootstrap_password_file(data_dir)
        log.warning(
            "[BOOTSTRAP] superadmin %s — credential reconciled to "
            "AUTH_LOCAL_BOOTSTRAP_PASSWORD env var",
            bootstrap_email,
        )
    else:
        log.info(
            "[BOOTSTRAP] superadmin %s — credential already provisioned; "
            "set AUTH_LOCAL_BOOTSTRAP_PASSWORD to reset to a known value",
            bootstrap_email,
        )
