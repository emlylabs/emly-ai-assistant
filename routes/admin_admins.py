"""Admin lifecycle management — list/update/delete admins, manage pending invites.

Replaces ``routes/admin_users_mgmt.py``. The legacy invite-by-token flow is
gone — identity now comes from the configured OIDC provider, and bot
assignments come from the ``pending_admin`` table (consumed on first matching
IdP login). Operators stage new admins via ``POST /admins/pending``.

All routes are gated on ``require_superadmin`` from ``services/authz``.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

from models.admin_users import AdminUserModel, AdminUsers
from models.pending_admins import PendingAdminModel, PendingAdmins
from services.auth.dependencies import get_admin
from services.authz import require_superadmin

log = logging.getLogger(__name__)

router = APIRouter()


# -------- response models --------


class AdminListResponse(BaseModel):
    admins: List[AdminUserModel]
    pending: List[PendingAdminModel]


class AdminUpdateRequest(BaseModel):
    is_active: Optional[bool] = None
    is_superadmin: Optional[bool] = None


class PendingInviteRequest(BaseModel):
    email: EmailStr
    is_superadmin: bool = False
    bot_assignments: List[dict] = []


# -------- listing --------


@router.get("/admins", response_model=AdminListResponse)
async def list_admins(admin: AdminUserModel = Depends(get_admin)) -> AdminListResponse:
    require_superadmin(admin)
    return AdminListResponse(
        admins=AdminUsers.list(),
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

    if payload.is_active is not None:
        AdminUsers.set_active(admin_id, payload.is_active)
    if payload.is_superadmin is not None:
        AdminUsers.set_superadmin(admin_id, payload.is_superadmin)

    fresh = AdminUsers.get_by_id(admin_id)
    if fresh is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "admin_not_found"})
    log.info("admin.updated by=%s target=%s active=%s superadmin=%s",
             admin.id, admin_id, payload.is_active, payload.is_superadmin)
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
    from models.admin_users import AdminUser
    AdminUser.delete().where(AdminUser.id == admin_id).execute()
    log.info("admin.deleted by=%s target=%s", admin.id, admin_id)


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
    require_superadmin(admin)
    if AdminUsers.get_by_email(payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "admin_already_exists"},
        )
    if PendingAdmins.get_active_by_email(payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "pending_already_exists"},
        )
    pending = PendingAdmins.create(
        email=payload.email,
        invited_by=admin.id,
        is_superadmin=payload.is_superadmin,
        bot_assignments=payload.bot_assignments,
    )
    log.info("admin.pending.created by=%s email=%s superadmin=%s",
             admin.id, payload.email, payload.is_superadmin)
    return pending


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
