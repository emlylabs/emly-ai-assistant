"""End-user action persistence (bot-scoped)."""
from __future__ import annotations

import logging

from fastapi import HTTPException, status

from models.emly_messages import EMLYMessages
from models.emly_user_action import EMLYUserActions, USER_ACTIONS

log = logging.getLogger(__name__)


async def create_event(
    bot_id: str,
    user_id: str,
    session_id: str,
    message_id: str,
    action_name: str,
    action_value: str,
    action_payload: dict,
) -> EMLYUserActions:
    if message_id:
        message = EMLYMessages.get_message_by_id(bot_id, message_id)
        if not message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message with ID '{message_id}' not found",
            )

    return USER_ACTIONS.insert_new_action(
        bot_id=bot_id,
        user_id=user_id,
        session_id=session_id,
        message_id=message_id,
        action_name=action_name,
        action_value=action_value,
        action_payload=action_payload,
    )
