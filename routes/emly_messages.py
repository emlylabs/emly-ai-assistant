import csv
import logging
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse

from config import SRC_LOG_LEVELS
from models.emly_messages import EMLYMessages
from utils.constants import ERROR_MESSAGES
from services.auth.dependencies import get_admin as get_admin_user

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


async def get_messages(bot_id: str, user_id, session_id, skip: int = 0, limit: int = 50):
    return EMLYMessages.get_messages(bot_id, user_id, session_id, skip, limit)


async def get_emly_message_by_id(bot_id: str, message_id: int):
    try:
        message = EMLYMessages.get_message_by_id(bot_id, message_id)
        if message:
            return message
        return JSONResponse(
            status_code=404,
            content={"message": f"Message having id '{message_id}' not found"},
        )
    except Exception:
        log.exception("Exception occurred while getting the message id=%s", message_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Exception occurred while getting the message",
        )


async def delete_message_by_id(bot_id: str, message_id: int):
    result = EMLYMessages.delete_message_by_id(bot_id, message_id)
    if result:
        return True
    return JSONResponse(
        status_code=404,
        content={"message": f"Message having id '{message_id}' not found"},
    )


async def get_messages_as_csv(
    bot_id: str,
    user_id: Optional[str],
    session_id: Optional[str],
    from_timestamp: Optional[datetime] = Query(None),
    to_timestamp: Optional[datetime] = Query(None),
    after_message_id: Optional[int] = None,
    user=Depends(get_admin_user),
):
    try:
        file_path = f"/tmp/messages_{user_id}.csv"
        with open(file_path, mode="w", newline="") as file:
            writer = csv.writer(file)
            messages = EMLYMessages.get_messages_from_to_as_csv(
                bot_id=bot_id,
                user_id=user_id,
                session_id=session_id,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
                after_message_id=after_message_id,
            )
            if messages:
                writer.writerow([
                    "message_id", "user_id", "session_id", "message",
                    "role", "created_on", "not_useful", "page", "topic",
                    "first_name", "last_name", "email", "phone",
                    "country", "city", "region", "latitude", "longitude",
                ])
                for message in messages:
                    writer.writerow([
                        message.message_id,
                        message.user_id,
                        message.session_id,
                        message.message,
                        message.role,
                        message.created_on,
                        message.not_useful,
                        message.page,
                        message.topic,
                        message.first_name,
                        message.last_name,
                        message.email,
                        message.phone,
                        message.country,
                        message.city,
                        message.region,
                        message.latitude,
                        message.longitude,
                    ])
            return FileResponse(file_path, media_type="text/csv", filename="messages.csv")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": f"Exception occurred while generating the csv: {str(e)}"},
        )
