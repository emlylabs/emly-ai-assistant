"""Dashboard endpoints for the new Next.js admin UI.

All routes here are protected via the OIDC `get_admin` dependency and live
under `/api/admin/...`. They return clean, paginated JSON tailored for the
UI rather than reusing the legacy `/api/v1` admin handlers (which have
overlapping route paths and were designed for a different consumer).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel

from models.admin_users import AdminUserModel, AdminUsers
from models.emly_messages import EMLYMessageModel, EMLYMessages
from models.emly_users import EMLYUserModel, EMLYUsers
from services.auth.dependencies import get_admin
from routes.actions import get_config_json_file, save_config_json_file
from utils.dependencies import invalidate_agent_service
from utils.utils import ImportData, ImportStatus

log = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# A single import-job manager shared with the legacy router. We use a module
# import here so both routers reference the same instance.
# ---------------------------------------------------------------------------
import_data_manager = ImportData()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class DashboardStats(BaseModel):
    bot_id: str
    admin_count: int
    end_user_count: int
    message_count: int
    last_import: Optional[Dict[str, Any]] = None


class MessageListResponse(BaseModel):
    items: List[EMLYMessageModel]
    total: int
    skip: int
    limit: int


class UserListResponse(BaseModel):
    items: List[EMLYUserModel]
    total: int
    skip: int
    limit: int


class UpdateMessageRequest(BaseModel):
    not_useful: Optional[bool] = None


class ConfigPayload(BaseModel):
    config: Dict[str, Any]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@router.get("/dashboard/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    bot_id: str = Query(...),
    _user: AdminUserModel = Depends(get_admin),
):
    cfg = get_config_json_file(bot_id) or {}
    return DashboardStats(
        bot_id=bot_id,
        admin_count=AdminUsers.count(),
        end_user_count=EMLYUsers.count(bot_id),
        message_count=EMLYMessages.count_all(bot_id),
        last_import=cfg.get("last_import_status"),
    )


# ---------------------------------------------------------------------------
# Messages (conversations)
# ---------------------------------------------------------------------------
@router.get("/messages", response_model=MessageListResponse)
async def list_messages(
    bot_id: str = Query(...),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    _user: AdminUserModel = Depends(get_admin),
):
    return MessageListResponse(
        items=EMLYMessages.list_all(bot_id, user_id=user_id, session_id=session_id, skip=skip, limit=limit),
        total=EMLYMessages.count_all(bot_id, user_id=user_id, session_id=session_id),
        skip=skip,
        limit=limit,
    )


@router.get("/messages/{message_id}", response_model=EMLYMessageModel)
async def get_message(
    message_id: int,
    bot_id: str = Query(...),
    _user: AdminUserModel = Depends(get_admin),
):
    msg = EMLYMessages.get_message_by_id(bot_id, message_id)
    if msg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return msg


@router.patch("/messages/{message_id}", response_model=EMLYMessageModel)
async def update_message(
    message_id: int,
    payload: UpdateMessageRequest,
    bot_id: str = Query(...),
    _user: AdminUserModel = Depends(get_admin),
):
    if payload.not_useful is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update")
    updated = EMLYMessages.update_emly_message_by_id(bot_id, message_id, payload.not_useful)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return updated


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: int,
    bot_id: str = Query(...),
    _user: AdminUserModel = Depends(get_admin),
):
    if not EMLYMessages.delete_message_by_id(bot_id, message_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")


# ---------------------------------------------------------------------------
# Bot end users
# ---------------------------------------------------------------------------
@router.get("/end-users", response_model=UserListResponse)
async def list_end_users(
    bot_id: str = Query(...),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _user: AdminUserModel = Depends(get_admin),
):
    return UserListResponse(
        items=EMLYUsers.get_users(bot_id, skip=skip, limit=limit),
        total=EMLYUsers.count(bot_id),
        skip=skip,
        limit=limit,
    )


@router.get("/end-users/{user_id}", response_model=EMLYUserModel)
async def get_end_user(
    user_id: str,
    bot_id: str = Query(...),
    _user: AdminUserModel = Depends(get_admin),
):
    u = EMLYUsers.get_user_by_id(bot_id, user_id)
    if u is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return u


# ---------------------------------------------------------------------------
# Bot config (emly_config.json)
# ---------------------------------------------------------------------------
@router.get("/config", response_model=Dict[str, Any])
async def get_config(
    bot_id: str = Query(...),
    _user: AdminUserModel = Depends(get_admin),
):
    return get_config_json_file(bot_id) or {}


@router.put("/config", response_model=Dict[str, Any])
async def put_config(
    payload: ConfigPayload,
    bot_id: str = Query(...),
    _user: AdminUserModel = Depends(get_admin),
):
    log.info("Saving updated bot config (%d top-level keys) bot=%s", len(payload.config), bot_id)
    saved = save_config_json_file(payload.config, bot_id)
    invalidate_agent_service(bot_id)
    return saved or payload.config


# ---------------------------------------------------------------------------
# Import / ingestion
# ---------------------------------------------------------------------------
@router.get("/import/status")
async def import_status(
    bot_id: str = Query(...),
    _user: AdminUserModel = Depends(get_admin),
):
    cfg = get_config_json_file(bot_id) or {}
    request_id = import_data_manager.current_request_id

    if not request_id:
        last_status = cfg.get("last_import_status")
        if last_status:
            if last_status.get("status") == ImportStatus.COMPLETED.value:
                last_status["progress"] = 100
            return last_status
        return {"status": ImportStatus.NOT_FOUND.value}

    job_status = import_data_manager.get_job_status(request_id, bot_id)
    return {
        "request_id": request_id,
        "status": job_status["status"],
        "progress": job_status.get("progress", 0),
        "error": job_status.get("error"),
        **{
            k: job_status[k]
            for k in [
                "total_documents",
                "completed_documents",
                "new_documents",
                "deleted_documents",
                "dataset_name",
                "last_updated",
            ]
            if k in job_status
        },
    }


