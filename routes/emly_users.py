import traceback
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import logging

from models.emly_users import EMLYUsers
from config import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


async def get_emly_users(bot_id: str, skip: int = 0, limit: int = 50):
    return EMLYUsers.get_users(bot_id, skip, limit)


class EMLYUserResponse(BaseModel):
    id: str
    first_name: Optional[str]
    last_name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    ip: str
    browser: str
    timestamp: int
    created_on: Optional[datetime]
    updated_on: Optional[datetime]


class EMLYResponse(BaseModel):
    user: EMLYUserResponse
    message: Optional[str] = None
    status: Optional[str] = None
    status_code: Optional[int] = None


async def get_emly_user_by_id(bot_id: str, user_id: str):
    try:
        user = EMLYUsers.get_user_by_id(bot_id, user_id)
        if user:
            return EMLYResponse(user=EMLYUserResponse(**user.model_dump()), message="Success")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"message": f"User not found having id '{user_id}'"},
        )
    except Exception as e:
        traceback.print_exception(e)
        raise HTTPException(
            status_code=500,
            detail={"message": f"Exception occurred: {e.__str__()}", "status": "error"},
        )
