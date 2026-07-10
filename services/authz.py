"""Centralised authorization predicates.

Pure functions of ``(admin, target)`` — never reads the request. Routes call
these to gate access; an ``HTTPException`` is the only way these functions fail.

Role hierarchy:
    owner  ⊇ admin ⊇ viewer
    is_superadmin → bypasses every bot-scoped check.

Membership rows live in ``admin_bot_membership``. The ``ROLES`` constant lists
the canonical names — anything outside the list is rejected at write time
(``AdminBotMemberships.grant``).
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from models.admin_bot_memberships import (
    AdminBotMembershipModel,
    AdminBotMemberships,
    ROLES,
)
from models.admin_users import AdminUserModel

log = logging.getLogger(__name__)

# Role tiers — each higher tier passes the checks of every lower tier.
_WRITER_ROLES = {"owner", "admin"}
_READER_ROLES = {"owner", "admin", "viewer"}


def _forbidden(code: str, **extra) -> HTTPException:
    body = {"code": code}
    body.update(extra)
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=body)


def require_superadmin(admin: AdminUserModel) -> None:
    """Raises 403 unless the admin has the global ``is_superadmin`` flag."""
    if not admin.is_superadmin:
        raise _forbidden("not_superadmin")


def require_member(admin: AdminUserModel, bot_id: str) -> AdminBotMembershipModel:
    """Returns the membership row if the admin has any role on the bot, else 403.

    Superadmins always pass; the membership row may not exist for them.
    Returns a synthetic ``AdminBotMembershipModel`` with role=``"owner"`` for
    superadmins so callers that need ``.role`` always get a value.
    """
    if admin.is_superadmin:
        log.info("authz.superadmin_bypass admin=%s bot=%s action=member", admin.id, bot_id)
        return _synthetic_superadmin_membership(admin.id, bot_id)
    membership = AdminBotMemberships.get(admin.id, bot_id)
    if membership is None or membership.role not in _READER_ROLES:
        raise _forbidden("not_member", bot_id=bot_id)
    return membership


def require_writer(admin: AdminUserModel, bot_id: str) -> AdminBotMembershipModel:
    """Returns the membership row if the admin can write to the bot, else 403.

    Owners and admins pass; viewers do not. Superadmins bypass.
    """
    if admin.is_superadmin:
        log.info("authz.superadmin_bypass admin=%s bot=%s action=writer", admin.id, bot_id)
        return _synthetic_superadmin_membership(admin.id, bot_id)
    membership = AdminBotMemberships.get(admin.id, bot_id)
    if membership is None or membership.role not in _WRITER_ROLES:
        raise _forbidden("not_writer", bot_id=bot_id)
    return membership


def require_owner(admin: AdminUserModel, bot_id: str) -> AdminBotMembershipModel:
    """Returns the membership row if the admin is an owner of the bot, else 403."""
    if admin.is_superadmin:
        log.info("authz.superadmin_bypass admin=%s bot=%s action=owner", admin.id, bot_id)
        return _synthetic_superadmin_membership(admin.id, bot_id)
    membership = AdminBotMemberships.get(admin.id, bot_id)
    if membership is None or membership.role != "owner":
        raise _forbidden("not_owner", bot_id=bot_id)
    return membership


def has_role_at_least(admin: AdminUserModel, bot_id: str, role: str) -> bool:
    """Non-raising convenience for routes that need to branch on capability rather
    than gate. Returns True if the admin's role on ``bot_id`` is ≥ ``role``.

    Role ordering: owner(3) > admin(2) > viewer(1) > none(0).
    """
    if role not in ROLES:
        raise ValueError(f"unknown role: {role!r}")
    if admin.is_superadmin:
        return True
    membership = AdminBotMemberships.get(admin.id, bot_id)
    if membership is None:
        return False
    rank = {"viewer": 1, "admin": 2, "owner": 3}
    return rank[membership.role] >= rank[role]


def _synthetic_superadmin_membership(admin_id: str, bot_id: str) -> AdminBotMembershipModel:
    """Construct a virtual membership row for the superadmin bypass path.

    Returned to callers so they don't need to special-case ``None`` for
    superadmins. The ``id`` is empty (it's not a real DB row) and timestamps
    are sentinels — callers should never persist this object.
    """
    from datetime import datetime, timezone

    return AdminBotMembershipModel(
        id="",
        admin_id=admin_id,
        bot_id=bot_id,
        role="owner",
        created_at=datetime.fromtimestamp(0, tz=timezone.utc),
        updated_at=datetime.fromtimestamp(0, tz=timezone.utc),
    )
