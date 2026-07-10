"""resolve_or_create_admin — the seam between OIDC identity and our admin table.

Called from the FastAPI dependency after ``OidcProvider.verify_token`` returns
a ``Principal``. The resolver:

1. Looks up by ``(issuer, subject)``. If found, refreshes ``last_login_at`` /
   ``email`` / ``name`` and returns.
2. Looks up by ``email``. If found AND ``(issuer, subject)`` differ from the
   token, the call is refused **by default**: this is the email-takeover guard.
   Operators flip ``AUTH_ALLOW_EMAIL_RELINK=true`` for planned migration windows.
3. Else looks for a ``pending_admin`` row matching the email. If present and
   not consumed/expired, consumes it: creates the ``admin_user`` with
   ``is_superadmin`` and ``bot_assignments`` from the row, links the OIDC
   identity, applies memberships.
4. Else refuses with ``admin_not_provisioned`` so the UI can route to a
   "Request access" page.

We do not gate on the ``email_verified`` claim. Successful OIDC sign-in is
treated as proof the user controls the address — see ``OidcProvider`` for the
rationale. ``Principal.email_verified`` is therefore always ``True`` for OIDC
logins and the value is persisted as such.

Wrapped in ``DB.atomic()`` to make first-login double-insert races deterministic.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from db.db import DB
from models.admin_bot_memberships import AdminBotMemberships, ROLES
from models.admin_users import AdminUser, AdminUserModel, AdminUsers
from models.pending_admins import PendingAdmins
from services.auth.base import AuthError, Principal

log = logging.getLogger(__name__)


class AdminNotProvisioned(AuthError):
    """No matching admin and no pending invite — the IdP authenticated but we
    have no record authorising this user."""


class EmailLinkedToOtherProvider(AuthError):
    """The email exists under a different ``(issuer, subject)`` and relink is
    disabled. Refuses by default to prevent silent account takeover."""


def _allow_email_relink() -> bool:
    return os.environ.get("AUTH_ALLOW_EMAIL_RELINK", "").strip().lower() in ("1", "true", "yes", "on")


def _generate_admin_id() -> str:
    return f"adm-{uuid.uuid4()}"


def _apply_pending_assignments(admin_id: str, assignments: list[dict]) -> None:
    from models.bots import Bots  # late import to avoid circulars

    for entry in assignments:
        bot_id = entry.get("bot_id")
        role = entry.get("role", "viewer")
        if not bot_id or role not in ROLES:
            log.warning("Skipping invalid pending bot assignment: %r", entry)
            continue
        if Bots.get_by_id(bot_id) is None:
            log.warning("Skipping pending bot assignment for nonexistent bot %s", bot_id)
            continue
        membership_id = f"mbr-{uuid.uuid4()}"
        try:
            AdminBotMemberships.grant(membership_id, admin_id, bot_id, role)
        except Exception:
            log.exception(
                "Failed to apply pending bot assignment admin=%s bot=%s role=%s",
                admin_id, bot_id, role,
            )


def resolve_or_create_admin(principal: Principal) -> AdminUserModel:
    """Map an authenticated ``Principal`` to a row in ``admin_user``.

    Raises:
        ``EmailLinkedToOtherProvider`` — email exists under a different IdP and
            ``AUTH_ALLOW_EMAIL_RELINK`` is not set.
        ``AdminNotProvisioned`` — no existing admin and no pending invite.
    """
    with DB.atomic():
        # 1) Existing OIDC link.
        existing = AdminUsers.get_by_issuer_subject(principal.issuer, principal.subject)
        if existing is not None:
            AdminUsers.touch_login(existing.id, email=principal.email, name=principal.name)
            return _refetch(existing.id)

        # 2) Email already belongs to a different IdP link.
        by_email = AdminUsers.get_by_email(principal.email)
        if by_email is not None:
            existing_issuer = by_email.issuer
            existing_subject = by_email.subject
            different_link = (
                existing_issuer is not None
                and existing_subject is not None
                and (existing_issuer != principal.issuer or existing_subject != principal.subject)
            )
            if different_link and not _allow_email_relink():
                log.warning(
                    "Refusing email-relink for %s: existing issuer=%s subject=%s, "
                    "incoming issuer=%s subject=%s. Set AUTH_ALLOW_EMAIL_RELINK=true to override.",
                    principal.email, existing_issuer, existing_subject,
                    principal.issuer, principal.subject,
                )
                raise EmailLinkedToOtherProvider(
                    "email is in use by an account linked to a different identity provider"
                )
            # Either no prior link (admin pre-provisioned for the embedded
            # issuer or by pending-activation in a previous flow), or relink
            # is explicitly allowed. Either way, link this row.
            AdminUsers.link_to_idp(
                by_email.id,
                issuer=principal.issuer,
                subject=principal.subject,
                email_verified=principal.email_verified,
                name=principal.name,
            )
            AdminUsers.touch_login(by_email.id, email=principal.email, name=principal.name)
            log.info("Linked admin %s to issuer=%s subject=%s", by_email.id, principal.issuer, principal.subject)
            try:
                from services.audit import audit
                audit(
                    "admin.relinked",
                    admin_id=by_email.id,
                    target_type="admin",
                    target_id=by_email.id,
                    payload={
                        "from_issuer": existing_issuer,
                        "from_subject": existing_subject,
                        "to_issuer": principal.issuer,
                        "to_subject": principal.subject,
                    },
                )
            except Exception:
                pass
            return _refetch(by_email.id)

        # 3) Pending-admin activation.
        pending = PendingAdmins.get_active_by_email(principal.email)
        if pending is not None:
            admin_id = _generate_admin_id()
            AdminUsers.insert(
                id=admin_id,
                email=principal.email,
                password_hash="",  # OIDC-only; legacy login rejects empty hashes
                is_active=True,
                issuer=principal.issuer,
                subject=principal.subject,
                email_verified=principal.email_verified,
                is_superadmin=pending.is_superadmin,
                name=principal.name,
            )
            AdminUsers.touch_login(admin_id, email=principal.email, name=principal.name)
            _apply_pending_assignments(admin_id, pending.bot_assignments)
            PendingAdmins.consume(principal.email, by_admin_id=admin_id)
            _migrate_bootstrap_credential(principal.email, admin_id)
            log.info(
                "Activated pending admin %s as %s (superadmin=%s, %d bot assignment(s))",
                principal.email, admin_id, pending.is_superadmin, len(pending.bot_assignments),
            )
            try:
                from services.audit import audit
                audit(
                    "admin.created_from_pending",
                    admin_id=admin_id,
                    target_type="admin",
                    target_id=admin_id,
                    payload={
                        "email": principal.email,
                        "issuer": principal.issuer,
                        "is_superadmin": pending.is_superadmin,
                        "bot_assignments": len(pending.bot_assignments),
                    },
                )
            except Exception:
                pass
            return _refetch(admin_id)

        # 4) Authenticated stranger.
        log.info("Refusing login for %s: no existing admin and no pending invite", principal.email)
        raise AdminNotProvisioned("admin_not_provisioned")


def _refetch(admin_id: str) -> AdminUserModel:
    """Return the freshest projection of the row after our writes."""
    fresh = AdminUsers.get_by_id(admin_id)
    if fresh is None:
        # Should be unreachable inside the same transaction; defensive only.
        raise RuntimeError(f"admin {admin_id} disappeared after write")
    return fresh


def _migrate_bootstrap_credential(email: str, admin_id: str) -> None:
    """Move the bootstrap-time ``local_credential`` from ``bootstrap:<email>``
    to the newly-minted ``admin_id``.

    The bootstrap flow seeds a credential keyed on the email sentinel before
    the admin row exists; activation copies that credential under the real
    admin_id so subsequent logins authenticate against ``admin.id``.

    No-op if the embedded issuer is disabled or no sentinel credential exists.
    """
    sentinel_id = f"bootstrap:{email.lower()}"
    try:
        from models.local_credentials import LocalCredentials
    except Exception:
        return

    sentinel_hash = LocalCredentials.get_password_hash(sentinel_id)
    if sentinel_hash is None:
        return
    sentinel_row = LocalCredentials.get(sentinel_id)
    LocalCredentials.upsert(
        admin_id=admin_id,
        password_hash=sentinel_hash,
        must_change=sentinel_row.must_change if sentinel_row is not None else False,
    )
    LocalCredentials.delete(sentinel_id)
    log.info("Migrated bootstrap credential for %s onto admin_id=%s", email, admin_id)
