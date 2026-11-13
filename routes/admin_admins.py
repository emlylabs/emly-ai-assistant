"""Admin lifecycle management — list/update/delete admins, manage pending invites.

Replaces ``routes/admin_users_mgmt.py``. The legacy invite-by-token flow is
gone — identity now comes from the configured OIDC provider, and bot
assignments come from the ``pending_admin`` table (consumed on first matching
IdP login). Operators stage new admins via ``POST /admins/pending``.

All routes are gated on ``require_superadmin`` from ``services/authz``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, field_validator

from db.db import DB
from models.admin_bot_memberships import AdminBotMemberships, ROLES
from models.admin_users import AdminUserModel, AdminUsers
from models.bots import Bots
from models.pending_admins import (
    KIND_SPACE_INVITE,
    KIND_SUPERADMIN_LEGACY,
    STATUS_ACCEPTED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_REVOKED,
    PendingAdminModel,
    PendingAdmins,
)
from services.audit import audit
from services.auth.dependencies import get_admin
from services.authz import require_owner, require_superadmin

log = logging.getLogger(__name__)

router = APIRouter()


# -------- response models --------


class AdminListMembership(BaseModel):
    bot_id: str
    role: str


class AdminListItem(AdminUserModel):
    """Row in the admin list. Mirrors ``AdminUserModel`` plus the admin's
    bot memberships so the matrix link can show "N bots" without an
    extra fetch per row."""

    memberships: List[AdminListMembership] = []


class AdminListResponse(BaseModel):
    admins: List[AdminListItem]
    pending: List[PendingAdminModel]


class AdminUpdateRequest(BaseModel):
    is_active: Optional[bool] = None
    is_superadmin: Optional[bool] = None


class PendingBotAssignment(BaseModel):
    """Validated per-bot assignment for a pending invite.

    Kept on ``bot_id`` (not slug) because the existing API contract is
    UUID-based — the UI's create-pending form already resolves the slug
    before calling. Role is locked to ``ROLES`` so a typo fails at invite
    time instead of silently dropping at first-login activation.
    """

    bot_id: str = Field(min_length=1)
    role: str

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        if value not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        return value


class PendingInviteRequest(BaseModel):
    email: EmailStr
    is_superadmin: bool = False
    bot_assignments: List[PendingBotAssignment] = []


# -------- listing --------


@router.get("/admins", response_model=AdminListResponse)
async def list_admins(admin: AdminUserModel = Depends(get_admin)) -> AdminListResponse:
    require_superadmin(admin)
    admins = AdminUsers.list()
    items: List[AdminListItem] = []
    for a in admins:
        mem = AdminBotMemberships.list_for_admin(a.id)
        items.append(
            AdminListItem(
                **a.model_dump(),
                memberships=[
                    AdminListMembership(bot_id=m.bot_id, role=m.role) for m in mem
                ],
            )
        )
    return AdminListResponse(
        admins=items,
        pending=PendingAdmins.list_pending(),
    )


# -------- update --------


@router.patch("/admins/{admin_id}", response_model=AdminUserModel)
async def update_admin(
    admin_id: str,
    payload: AdminUpdateRequest,
    admin: AdminUserModel = Depends(get_admin),
) -> AdminUserModel:
    require_superadmin(admin)
    target = AdminUsers.get_by_id(admin_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "admin_not_found"})

    # Last-superadmin protection: forbid clearing is_superadmin / deactivating
    # the only remaining active superadmin (including self-deactivation).
    if (payload.is_superadmin is False or payload.is_active is False) and target.is_superadmin and target.is_active:
        if AdminUsers.count_superadmins_active() <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "last_active_superadmin"},
            )

    before = {"is_active": target.is_active, "is_superadmin": target.is_superadmin}
    if payload.is_active is not None:
        AdminUsers.set_active(admin_id, payload.is_active)
    if payload.is_superadmin is not None:
        AdminUsers.set_superadmin(admin_id, payload.is_superadmin)

    fresh = AdminUsers.get_by_id(admin_id)
    if fresh is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "admin_not_found"})
    log.info("admin.updated by=%s target=%s active=%s superadmin=%s",
             admin.id, admin_id, payload.is_active, payload.is_superadmin)
    audit(
        "admin.updated",
        admin_id=admin.id,
        target_type="admin",
        target_id=admin_id,
        payload={
            "is_active": payload.is_active,
            "is_superadmin": payload.is_superadmin,
            # Before/after of the fields that actually changed.
            "changed": {
                field: {"before": before[field], "after": getattr(fresh, field)}
                for field in before
                if before[field] != getattr(fresh, field)
            },
        },
    )
    return fresh


# -------- delete --------


@router.delete("/admins/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin(
    admin_id: str,
    admin: AdminUserModel = Depends(get_admin),
) -> None:
    require_superadmin(admin)
    target = AdminUsers.get_by_id(admin_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "admin_not_found"})
    if target.is_superadmin and target.is_active and AdminUsers.count_superadmins_active() <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "last_active_superadmin"},
        )
    # Soft-delete: tombstone the row so audit_log.admin_id references stay
    # resolvable. Memberships cascade off the ``is_active=False`` set inside
    # ``soft_delete`` (live counts treat the admin as gone); the row itself
    # stays for forensic lookups via ``get_by_id_including_deleted``.
    if not AdminUsers.soft_delete(admin_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "admin_not_found"})
    # Revoke memberships explicitly — they're decoupled from is_active and
    # would otherwise still grant access if the tombstone is ever cleared.
    from models.admin_bot_memberships import AdminBotMembership
    AdminBotMembership.delete().where(AdminBotMembership.admin == admin_id).execute()
    log.info("admin.deleted by=%s target=%s", admin.id, admin_id)
    audit(
        "admin.deleted",
        admin_id=admin.id,
        target_type="admin",
        target_id=admin_id,
        payload={"email": target.email},
    )


# -------- pending invites --------


@router.get("/admins/pending", response_model=List[PendingAdminModel])
async def list_pending(admin: AdminUserModel = Depends(get_admin)) -> List[PendingAdminModel]:
    require_superadmin(admin)
    return PendingAdmins.list_pending()


@router.post("/admins/pending", response_model=PendingAdminModel, status_code=status.HTTP_201_CREATED)
async def create_pending(
    payload: PendingInviteRequest,
    admin: AdminUserModel = Depends(get_admin),
) -> PendingAdminModel:
    """Superadmin-only platform invite. Mints a tokenised row and emails
    the recipient an accept link. The picker UI shows it among any
    other pending invites; on the first OIDC login the resolver creates
    the default space and leaves the row pending for explicit accept."""
    require_superadmin(admin)
    if AdminUsers.get_by_email(payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "admin_already_exists"},
        )
    if PendingAdmins.list_pending_for_email(payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "pending_already_exists"},
        )
    # Validate each ``bot_id`` exists and is not soft-deleted. The
    # consume-time path in services/auth/user_resolver silently skips
    # invalid entries — surfacing the failure at invite time saves the
    # operator from debugging missing access after activation.
    bot_assignments_dicts: List[dict] = []
    for entry in payload.bot_assignments:
        bot = Bots.get_by_id(entry.bot_id)
        if bot is None or bot.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_bot_id", "bot_id": entry.bot_id},
            )
        bot_assignments_dicts.append({"bot_id": entry.bot_id, "role": entry.role})
    import secrets

    primary_bot_id = bot_assignments_dicts[0]["bot_id"] if bot_assignments_dicts else None
    primary_bot_name = (
        Bots.get_by_id(primary_bot_id).name if primary_bot_id else "Emly Labs"
    )
    pending = PendingAdmins.create(
        email=payload.email,
        invited_by=admin.id,
        is_superadmin=payload.is_superadmin,
        bot_assignments=bot_assignments_dicts,
        kind=KIND_SPACE_INVITE,
        token=secrets.token_urlsafe(32),
        target_bot_id=primary_bot_id,
    )
    log.info("admin.pending.created by=%s email=%s superadmin=%s",
             admin.id, payload.email, payload.is_superadmin)
    audit(
        "pending.created",
        admin_id=admin.id,
        target_type="pending_admin",
        target_id=pending.id,
        payload={
            "email": payload.email,
            "is_superadmin": payload.is_superadmin,
            "bot_assignments": len(payload.bot_assignments),
        },
    )
    _try_send_invite_email(
        pending=pending,
        inviter=admin,
        space_name=primary_bot_name,
        role=bot_assignments_dicts[0]["role"] if bot_assignments_dicts else "admin",
    )
    return pending


# ---------------------------------------------------------------------------
# Helpers shared by the invite routes.
# ---------------------------------------------------------------------------


def _try_send_invite_email(
    *,
    pending: PendingAdminModel,
    inviter: AdminUserModel,
    space_name: str,
    role: str,
) -> None:
    if not pending.token:
        return
    try:
        from services.invite_email import schedule_invite_email

        schedule_invite_email(
            token=pending.token,
            email=pending.email,
            inviter_name=inviter.name or inviter.email,
            inviter_email=inviter.email,
            space_name=space_name,
            role=role,
            expires_at=pending.expires_at,
        )
    except Exception:
        log.exception("schedule_invite_email failed for pending=%s", pending.id)


@router.delete("/admins/pending/{email}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_pending(
    email: str,
    admin: AdminUserModel = Depends(get_admin),
) -> None:
    require_superadmin(admin)
    if PendingAdmins.get_active_by_email(email) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "pending_not_found"})
    PendingAdmins.revoke(email)
    log.info("admin.pending.revoked by=%s email=%s", admin.id, email)
    audit(
        "pending.revoked",
        admin_id=admin.id,
        target_type="pending_admin",
        payload={"email": email},
    )


# ---------------------------------------------------------------------------
# Cross-bot membership matrix.
#
# The bot-scoped routes in ``routes/admin_bots.py`` are owner-gated on a
# *single* bot. These admin-scoped routes give superadmins a workspace-wide
# view (and bulk-edit) of one admin's access. They share the last-owner
# protection from the bot-scoped routes — revoking an owner that would leave
# a bot ownerless returns 409 with details on which bot blocked the change.
# ---------------------------------------------------------------------------


class MembershipMatrixRow(BaseModel):
    bot_id: str
    bot_slug: str
    bot_name: str
    role: Optional[str] = None  # null when the admin has no membership on this bot


class MembershipMatrixResponse(BaseModel):
    admin: AdminUserModel
    rows: List[MembershipMatrixRow]


@router.get("/admins/{admin_id}/memberships", response_model=MembershipMatrixResponse)
async def get_admin_memberships(
    admin_id: str,
    admin: AdminUserModel = Depends(get_admin),
) -> MembershipMatrixResponse:
    """Return the full bot list with this admin's role on each (null when
    not a member). Always lists every active bot so the matrix UI doesn't
    need a second fetch to know what's available to grant.
    """
    require_superadmin(admin)
    target = AdminUsers.get_by_id(admin_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "admin_not_found"})
    existing = {m.bot_id: m.role for m in AdminBotMemberships.list_for_admin(admin_id)}
    rows: List[MembershipMatrixRow] = []
    for bot in Bots.list_active():
        rows.append(
            MembershipMatrixRow(
                bot_id=bot.id,
                bot_slug=bot.slug,
                bot_name=bot.name,
                role=existing.get(bot.id),
            )
        )
    return MembershipMatrixResponse(admin=target, rows=rows)


class MembershipAssignment(BaseModel):
    bot_slug: str
    role: Optional[str] = Field(default=None, description="Set to null to revoke.")


class BulkMembershipRequest(BaseModel):
    assignments: List[MembershipAssignment]


class BulkMembershipResult(BaseModel):
    bot_slug: str
    status: str  # "granted" | "updated" | "revoked" | "unchanged" | "error"
    detail: Optional[str] = None


class BulkMembershipResponse(BaseModel):
    results: List[BulkMembershipResult]


@router.post("/admins/{admin_id}/memberships", response_model=BulkMembershipResponse)
async def bulk_update_admin_memberships(
    admin_id: str,
    payload: BulkMembershipRequest,
    admin: AdminUserModel = Depends(get_admin),
) -> BulkMembershipResponse:
    """Apply a batch of grant / role-change / revoke operations to one admin.

    Atomicity: each assignment is processed independently so a single
    last-owner conflict on one bot doesn't undo grants to the others. The
    response surfaces per-row status so the UI can show a partial-success
    state instead of either-all-or-nothing.
    """
    require_superadmin(admin)
    target = AdminUsers.get_by_id(admin_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "admin_not_found"})
    results: List[BulkMembershipResult] = []
    # Before/after role per bot for the audit diff (C7).
    role_changes: dict[str, dict] = {}
    for assignment in payload.assignments:
        bot = Bots.get_by_slug(assignment.bot_slug)
        if bot is None or bot.is_deleted:
            results.append(
                BulkMembershipResult(
                    bot_slug=assignment.bot_slug, status="error", detail="bot_not_found",
                )
            )
            continue
        existing = AdminBotMemberships.get(admin_id, bot.id)
        desired_role = assignment.role
        role_changes[assignment.bot_slug] = {
            "from_role": existing.role if existing else None,
            "to_role": desired_role,
        }
        if desired_role is not None and desired_role not in ROLES:
            results.append(
                BulkMembershipResult(
                    bot_slug=assignment.bot_slug, status="error", detail="invalid_role",
                )
            )
            continue
        try:
            with DB.atomic():
                if desired_role is None:
                    if existing is None:
                        results.append(BulkMembershipResult(bot_slug=assignment.bot_slug, status="unchanged"))
                        continue
                    if existing.role == "owner" and AdminBotMemberships.count_owners(bot.id) <= 1:
                        results.append(
                            BulkMembershipResult(
                                bot_slug=assignment.bot_slug,
                                status="error",
                                detail="last_owner_protected",
                            )
                        )
                        continue
                    AdminBotMemberships.revoke(admin_id, bot.id)
                    results.append(BulkMembershipResult(bot_slug=assignment.bot_slug, status="revoked"))
                else:
                    if existing is None:
                        AdminBotMemberships.grant(
                            id=f"mbr-{uuid.uuid4()}",
                            admin_id=admin_id,
                            bot_id=bot.id,
                            role=desired_role,
                        )
                        results.append(BulkMembershipResult(bot_slug=assignment.bot_slug, status="granted"))
                    elif existing.role == desired_role:
                        results.append(BulkMembershipResult(bot_slug=assignment.bot_slug, status="unchanged"))
                    else:
                        if existing.role == "owner" and desired_role != "owner":
                            if AdminBotMemberships.count_owners(bot.id) <= 1:
                                results.append(
                                    BulkMembershipResult(
                                        bot_slug=assignment.bot_slug,
                                        status="error",
                                        detail="last_owner_protected",
                                    )
                                )
                                continue
                        AdminBotMemberships.update_role(admin_id, bot.id, desired_role)
                        results.append(BulkMembershipResult(bot_slug=assignment.bot_slug, status="updated"))
        except Exception as exc:
            log.exception("bulk membership write failed admin=%s bot=%s", admin_id, bot.id)
            results.append(
                BulkMembershipResult(
                    bot_slug=assignment.bot_slug,
                    status="error",
                    detail="internal_error",
                )
            )
    log.info("admin.memberships.bulk by=%s target=%s changes=%d", admin.id, admin_id, len(results))
    audit(
        "membership.bulk",
        admin_id=admin.id,
        target_type="admin",
        target_id=admin_id,
        payload={
            "results": [
                {"bot_slug": r.bot_slug, "status": r.status, "detail": r.detail,
                 **(role_changes.get(r.bot_slug, {}) if r.status in ("granted", "updated", "revoked") else {})}
                for r in results
            ],
        },
    )
    return BulkMembershipResponse(results=results)


# ---------------------------------------------------------------------------
# Transfer ownership — single-call atomic swap that avoids the
# "I demoted myself first and got stuck" pitfall the docs called out.
# ---------------------------------------------------------------------------


class TransferOwnershipRequest(BaseModel):
    bot_slug: str
    new_owner_admin_id: str
    # Default is to keep the current owner as ``admin`` so the transfer
    # doesn't unintentionally cut their access. The UI can pass ``revoke``
    # to fully hand off, or ``viewer`` for read-only retention.
    leaving_owner_new_role: Optional[str] = Field(
        default="admin",
        description="Role to assign the previous owner: admin / viewer / null (revoke).",
    )


class TransferOwnershipResponse(BaseModel):
    bot_slug: str
    new_owner_admin_id: str
    previous_owner_admin_id: Optional[str] = None
    previous_owner_new_role: Optional[str] = None


@router.post("/bots/{slug}/transfer-ownership", response_model=TransferOwnershipResponse)
async def transfer_bot_ownership(
    slug: str,
    payload: TransferOwnershipRequest,
    admin: AdminUserModel = Depends(get_admin),
) -> TransferOwnershipResponse:
    """Atomically promote ``new_owner_admin_id`` to owner and demote the
    requester to ``leaving_owner_new_role`` (or revoke them entirely).

    Authz: caller must be the current owner of the bot — only owners can
    initiate a handoff. Superadmins can call this too via the standard
    bypass in ``services/authz``.
    """
    if payload.bot_slug != slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bot_slug in body must match path",
        )
    bot = Bots.get_by_slug(slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")

    if not admin.is_superadmin:
        my_membership = AdminBotMemberships.get(admin.id, bot.id)
        if my_membership is None or my_membership.role != "owner":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only owners can transfer ownership",
            )

    new_owner = AdminUsers.get_by_id(payload.new_owner_admin_id)
    if new_owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="New owner not found")
    leaving_role = payload.leaving_owner_new_role
    if leaving_role is not None and leaving_role not in ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid leaving role")
    if leaving_role == "owner":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use grant_membership to add another owner without transferring",
        )

    # Single transaction: promote → demote (or revoke). The owner-count
    # invariant trivially holds because we always create the new owner
    # before we touch the old one.
    with DB.atomic():
        new_membership = AdminBotMemberships.get(payload.new_owner_admin_id, bot.id)
        if new_membership is None:
            AdminBotMemberships.grant(
                id=f"mbr-{uuid.uuid4()}",
                admin_id=payload.new_owner_admin_id,
                bot_id=bot.id,
                role="owner",
            )
        elif new_membership.role != "owner":
            AdminBotMemberships.update_role(payload.new_owner_admin_id, bot.id, "owner")

        previous_owner_id: Optional[str] = None
        previous_owner_new_role: Optional[str] = None
        # Only demote/revoke the caller if they were the original owner.
        # Superadmins acting on someone else's bot leave existing owners alone.
        caller_membership = AdminBotMemberships.get(admin.id, bot.id)
        if caller_membership is not None and caller_membership.role == "owner" and admin.id != payload.new_owner_admin_id:
            previous_owner_id = admin.id
            if leaving_role is None:
                AdminBotMemberships.revoke(admin.id, bot.id)
                previous_owner_new_role = None
            else:
                AdminBotMemberships.update_role(admin.id, bot.id, leaving_role)
                previous_owner_new_role = leaving_role

    log.info(
        "bot.ownership.transferred by=%s bot=%s new_owner=%s old_role=%s",
        admin.id,
        bot.id,
        payload.new_owner_admin_id,
        previous_owner_new_role,
    )
    audit(
        "bot.ownership.transferred",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="admin",
        target_id=payload.new_owner_admin_id,
        payload={
            "previous_owner_admin_id": previous_owner_id,
            "previous_owner_new_role": previous_owner_new_role,
        },
    )
    return TransferOwnershipResponse(
        bot_slug=slug,
        new_owner_admin_id=payload.new_owner_admin_id,
        previous_owner_admin_id=previous_owner_id,
        previous_owner_new_role=previous_owner_new_role,
    )


# ---------------------------------------------------------------------------
# Space-level invitations — tokenised accept/reject UX.
#
# The legacy ``/admins/pending`` route stays superadmin-only and is for
# platform-wide invites (multi-bot grants, ``is_superadmin=True``).
# These routes scope to a single space and are open to bot owners.
# Both paths share the same ``pending_admin`` table with
# ``kind='space_invite'`` and a unique URL token.
# ---------------------------------------------------------------------------


class SpaceInviteRequest(BaseModel):
    email: EmailStr
    bot_slug: str = Field(min_length=1, max_length=64)
    role: str = Field(default="admin")

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        if value not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        return value


class SpaceInviteResponse(PendingAdminModel):
    """Pending row plus the click-through URL — convenience for the UI so
    it can show a copy-link button without re-deriving ``PUBLIC_BASE_URL``
    on the client."""

    invite_url: Optional[str] = None
    bot_slug: Optional[str] = None
    bot_name: Optional[str] = None


class InviteByTokenView(BaseModel):
    """Public-safe projection. ``inviter_email`` is only populated when the
    caller is authenticated; unauthenticated callers see name only to
    limit email enumeration via the token URL."""

    token: str
    status: str
    kind: str
    email: EmailStr
    role: str
    bot_slug: Optional[str] = None
    bot_name: Optional[str] = None
    is_superadmin: bool = False
    inviter_name: Optional[str] = None
    inviter_email: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None


class MyInviteView(BaseModel):
    """Row for the in-app "My invites" list."""

    token: str
    status: str
    email: EmailStr
    role: str
    bot_slug: Optional[str] = None
    bot_name: Optional[str] = None
    is_superadmin: bool = False
    inviter_email: Optional[str] = None
    inviter_name: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None


def _primary_assignment(p: PendingAdminModel) -> tuple[Optional[str], Optional[str]]:
    """Return ``(bot_id, role)`` for the lead assignment on the row.

    The new-style invite always carries a single assignment; legacy rows
    may have many but we only surface the first for UI summaries — the
    full list is still applied on accept.
    """
    if p.target_bot_id:
        for entry in p.bot_assignments:
            if entry.get("bot_id") == p.target_bot_id:
                return p.target_bot_id, entry.get("role", "viewer")
        return p.target_bot_id, "viewer"
    if p.bot_assignments:
        first = p.bot_assignments[0]
        return first.get("bot_id"), first.get("role", "viewer")
    return None, "admin"


def _bot_summary(bot_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not bot_id:
        return None, None
    bot = Bots.get_by_id(bot_id)
    if bot is None:
        return None, None
    return bot.slug, bot.name


def _build_my_invite_view(p: PendingAdminModel) -> MyInviteView:
    bot_id, role = _primary_assignment(p)
    bot_slug, bot_name = _bot_summary(bot_id)
    inviter = AdminUsers.get_by_id(p.invited_by) if p.invited_by else None
    return MyInviteView(
        token=p.token or "",
        status=p.status,
        email=p.email,
        role=role or "admin",
        bot_slug=bot_slug,
        bot_name=bot_name,
        is_superadmin=p.is_superadmin,
        inviter_email=inviter.email if inviter else None,
        inviter_name=inviter.name if inviter else None,
        created_at=p.created_at,
        expires_at=p.expires_at,
    )


@router.post(
    "/admins/invites",
    response_model=SpaceInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_space_invite(
    payload: SpaceInviteRequest,
    admin: AdminUserModel = Depends(get_admin),
) -> SpaceInviteResponse:
    """Bot-owner-scoped invite for a single space.

    Authz: caller must be ``owner`` of the target bot (or superadmin).
    Mints a tokenised ``pending_admin`` row and schedules an email.
    Recipients accept/reject from ``/accept-invite?token=...``.
    """
    bot = Bots.get_by_slug(payload.bot_slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "bot_not_found"},
        )
    require_owner(admin, bot.id)

    existing_admin = AdminUsers.get_by_email(payload.email)
    if existing_admin is not None:
        # Already-in-system — if they're already a member, refuse; else
        # we still allow a fresh invite so the same accept/reject UX
        # applies. The accept route grants the membership.
        already = AdminBotMemberships.get(existing_admin.id, bot.id)
        if already is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "already_member", "role": already.role},
            )

    # Refuse a duplicate space invite to the same email for the same
    # space — the existing row's token is still valid.
    for p in PendingAdmins.list_pending_for_email(payload.email, kind=KIND_SPACE_INVITE):
        if p.target_bot_id == bot.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "invite_already_pending", "token": p.token},
            )

    pending = PendingAdmins.create_space_invite(
        email=payload.email,
        invited_by=admin.id,
        target_bot_id=bot.id,
        role=payload.role,
    )

    log.info(
        "invite.space.created by=%s email=%s bot=%s role=%s",
        admin.id, payload.email, bot.id, payload.role,
    )
    audit(
        "invite.created",
        admin_id=admin.id,
        bot_id=bot.id,
        target_type="pending_admin",
        target_id=pending.id,
        payload={
            "email": payload.email,
            "role": payload.role,
            "bot_slug": bot.slug,
        },
    )

    _try_send_invite_email(
        pending=pending,
        inviter=admin,
        space_name=bot.name,
        role=payload.role,
    )

    from services.invite_email import build_accept_url

    invite_url = build_accept_url(pending.token) if pending.token else None
    return SpaceInviteResponse(
        **pending.model_dump(),
        invite_url=invite_url,
        bot_slug=bot.slug,
        bot_name=bot.name,
    )


@router.get("/admins/invites/mine", response_model=List[MyInviteView])
async def list_my_invites(
    admin: AdminUserModel = Depends(get_admin),
) -> List[MyInviteView]:
    """All pending invites visible to the signed-in admin (matched by
    email, case-insensitive). Powers the post-login picker and the
    in-app My invites tab."""
    rows = PendingAdmins.list_pending_for_email(admin.email)
    return [_build_my_invite_view(p) for p in rows]


@router.get("/admins/invites/by-token/{token}", response_model=InviteByTokenView)
async def get_invite_by_token(token: str) -> InviteByTokenView:
    """Render-time fetch for the public ``/accept-invite`` landing page.

    Token-only (no admin dep) so the user can see the space name +
    inviter before signing in. Returns 404 for unknown tokens and a
    410-style payload for terminal states so the UI can show a tailored
    message ("already accepted", "expired", ...).
    """
    row = PendingAdmins.get_by_token(token)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "invite_not_found"},
        )
    bot_id, role = _primary_assignment(row)
    bot_slug, bot_name = _bot_summary(bot_id)
    inviter = AdminUsers.get_by_id(row.invited_by) if row.invited_by else None
    return InviteByTokenView(
        token=row.token or token,
        status=row.status,
        kind=row.kind,
        email=row.email,
        role=role or "admin",
        bot_slug=bot_slug,
        bot_name=bot_name,
        is_superadmin=row.is_superadmin,
        inviter_name=inviter.name if inviter else None,
        # Inviter email is leaked over the public token URL, but it's
        # the same information the recipient already received via email.
        inviter_email=inviter.email if inviter else None,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


class AcceptInviteResponse(BaseModel):
    token: str
    status: str
    bot_slug: Optional[str] = None


def _row_expired(row: PendingAdminModel) -> bool:
    if row.expires_at is None:
        return False
    try:
        from datetime import datetime as _dt, timezone as _tz

        expires_at = row.expires_at
        if expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(_tz.utc).replace(tzinfo=None)
        return expires_at < _dt.utcnow()
    except Exception:
        return False


@router.post("/admins/invites/{token}/accept", response_model=AcceptInviteResponse)
async def accept_invite(
    token: str,
    admin: AdminUserModel = Depends(get_admin),
) -> AcceptInviteResponse:
    row = PendingAdmins.get_by_token(token)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "invite_not_found"},
        )
    if row.email.lower() != admin.email.lower():
        log.warning(
            "invite.accept email mismatch admin=%s invite_email=%s invite_id=%s",
            admin.id, row.email, row.id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "invite_email_mismatch"},
        )
    if row.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": f"invite_already_{row.status}"},
        )
    if _row_expired(row):
        # Promote to expired so subsequent reads see the terminal state.
        PendingAdmins.mark_revoked(row.id, admin.id)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "invite_expired"},
        )

    from services.auth.user_resolver import _apply_pending_assignments

    with DB.atomic():
        _apply_pending_assignments(admin.id, row.bot_assignments)
        if row.is_superadmin and not admin.is_superadmin:
            AdminUsers.set_superadmin(admin.id, True)
        PendingAdmins.mark_accepted(row.id, admin.id)

    bot_id, _ = _primary_assignment(row)
    bot_slug, _name = _bot_summary(bot_id)
    log.info("invite.accept by=%s invite_id=%s bot=%s", admin.id, row.id, bot_id)
    audit(
        "invite.accepted",
        admin_id=admin.id,
        bot_id=bot_id,
        target_type="pending_admin",
        target_id=row.id,
        payload={"role": _primary_assignment(row)[1], "bot_slug": bot_slug},
    )
    return AcceptInviteResponse(token=token, status=STATUS_ACCEPTED, bot_slug=bot_slug)


@router.post("/admins/invites/{token}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_invite(
    token: str,
    admin: AdminUserModel = Depends(get_admin),
) -> None:
    row = PendingAdmins.get_by_token(token)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "invite_not_found"},
        )
    if row.email.lower() != admin.email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "invite_email_mismatch"},
        )
    if row.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": f"invite_already_{row.status}"},
        )
    PendingAdmins.mark_rejected(row.id, admin.id)
    bot_id, _ = _primary_assignment(row)
    log.info("invite.reject by=%s invite_id=%s bot=%s", admin.id, row.id, bot_id)
    audit(
        "invite.rejected",
        admin_id=admin.id,
        bot_id=bot_id,
        target_type="pending_admin",
        target_id=row.id,
        payload={},
    )


@router.delete("/admins/invites/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    token: str,
    admin: AdminUserModel = Depends(get_admin),
) -> None:
    """Inviter / bot-owner / superadmin can revoke a pending invite.

    Soft revoke — the row stays for audit; the token returns 410 on the
    next click.
    """
    row = PendingAdmins.get_by_token(token)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "invite_not_found"},
        )
    if row.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": f"invite_already_{row.status}"},
        )
    is_inviter = (row.invited_by == admin.id)
    bot_id = row.target_bot_id
    is_bot_owner = False
    if bot_id is not None and not admin.is_superadmin and not is_inviter:
        membership = AdminBotMemberships.get(admin.id, bot_id)
        is_bot_owner = membership is not None and membership.role == "owner"
    if not (admin.is_superadmin or is_inviter or is_bot_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "invite_revoke_not_authorized"},
        )
    PendingAdmins.mark_revoked(row.id, admin.id)
    log.info("invite.revoke by=%s invite_id=%s bot=%s", admin.id, row.id, bot_id)
    audit(
        "invite.revoked",
        admin_id=admin.id,
        bot_id=bot_id,
        target_type="pending_admin",
        target_id=row.id,
        payload={"email": row.email},
    )


@router.get("/admins/invites/for-bot/{bot_slug}", response_model=List[MyInviteView])
async def list_invites_for_bot(
    bot_slug: str,
    admin: AdminUserModel = Depends(get_admin),
) -> List[MyInviteView]:
    """Pending invites that target a specific space. Used by the bot
    Members panel to surface outgoing invites with a revoke action."""
    bot = Bots.get_by_slug(bot_slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "bot_not_found"},
        )
    # Member-level access is enough to view; revoke is gated separately.
    if not admin.is_superadmin:
        from services.authz import require_member

        require_member(admin, bot.id)
    rows = [
        p
        for p in PendingAdmins.list_pending()
        if p.target_bot_id == bot.id and p.kind == KIND_SPACE_INVITE
    ]
    return [_build_my_invite_view(p) for p in rows]
