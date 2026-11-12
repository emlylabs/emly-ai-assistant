import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from services.agent_service import AgentService
from utils.dependencies import get_agent_service
from utils.schemas import AgentRequest

router = APIRouter()


def _chat_principal(bot_slug: str, request: Request):
    """Dependency: verified widget token for the bot in the path. Runs
    before body validation so a tokenless request gets 401, not 422."""
    from routes.widget import resolve_bot
    from services.auth.widget_auth import require_bearer, require_widget_principal

    require_bearer(request)
    return require_widget_principal(request, resolve_bot(bot_slug))

logger = logging.getLogger(__name__)


@router.post("/widget/{bot_slug}/chat", summary="Web widget chat (path-scoped to bot)")
async def widget_chat(
    bot_slug: str,
    request: AgentRequest,
    http_request: Request,
    agent_service: AgentService = Depends(get_agent_service),
    principal=Depends(_chat_principal),
):
    """Path-based chat surface for the embed widget.

    The bot is identified by ``{bot_slug}`` in the URL — the widget
    snippet bakes this in once at generation time. Returns 404 if the
    bot doesn't exist or is soft-deleted.
    """
    from routes.widget import resolve_bot
    from services.auth.widget_auth import ensure_same_user

    bot = resolve_bot(bot_slug)
    ensure_same_user(principal, request.user_id)

    # Session ids are minted client-side; refuse one that belongs to
    # another visitor so a token for user A can't continue user B's thread.
    from models.emly_sessions import EMLYSessions

    existing = EMLYSessions.get(bot.id, str(request.session_id))
    if existing is not None and existing.user_id and existing.user_id != principal.user_id:
        raise HTTPException(status_code=403, detail={"code": "session_not_owned"})

    from models.emly_users import EMLYUsers
    end_user_id = str(request.user_id) if request.user_id else None
    if end_user_id and EMLYUsers.get_user_by_id(bot.id, end_user_id) is None:
        client = http_request.client
        ip = client.host if client else ""
        browser = http_request.headers.get("user-agent", "")
        try:
            EMLYUsers.insert_new_user(
                bot_id=bot.id,
                id=end_user_id,
                first_name=None, last_name=None, email=None, phone=None,
                ip=ip,
                browser=browser,
                meta=None,
            )
        except Exception:
            if EMLYUsers.get_user_by_id(bot.id, end_user_id) is None:
                logger.exception("Failed to auto-create end-user for bot=%s user=%s", bot.id, end_user_id)
            else:
                logger.info("Concurrent insert for bot=%s user=%s — using existing row", bot.id, end_user_id)

    user_id = request.user_id
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    if not request.messages:
        raise HTTPException(status_code=400, detail="Message is required")
    if not request.timestamp:
        raise HTTPException(status_code=400, detail="Timestamp is required")

    if not agent_service.is_available():
        raise HTTPException(
            status_code=503,
            detail="Agent service is not available. Please check the configuration and ensure OPENAI_API_KEY is set.",
        )

    from models.bot_channels import BotChannels
    channel_id = BotChannels.get_default_web_widget_channel_id(bot.id)

    try:
        response = agent_service.process_message(
            bot_id=bot.id,
            user_id=str(user_id),
            session_id=str(request.session_id),
            message=request.messages[0].content,
            stream=request.stream,
            page_id=request.page_id or "default",
            channel_id=channel_id,
        )

        if not request.stream:
            response_text, citations = response

            def stream_non_stream():
                yield json.dumps({
                    "message": {"role": "assistant", "content": response_text},
                    "citations": [],
                    "done": False,
                    "done_reason": None,
                }) + "\n"
                yield json.dumps({
                    "message": {"role": "assistant", "content": ""},
                    "citations": citations,
                    "done": True,
                    "done_reason": "stop",
                    "message_id": 123,
                }) + "\n"

            return StreamingResponse(stream_non_stream())

        def stream_generator():
            for event in response:
                if event["type"] == "token":
                    yield json.dumps({
                        "message": {"role": "assistant", "content": event["data"]},
                        "citations": [],
                        "done": False,
                        "done_reason": None,
                    }) + "\n"
                elif event["type"] == "citations":
                    yield json.dumps({
                        "message": {"role": "assistant", "content": ""},
                        "citations": event["data"],
                        "done": True,
                        "done_reason": "stop",
                        "message_id": event.get("message_id", None),
                    }) + "\n"

        return StreamingResponse(stream_generator())

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error processing agent request")
        raise HTTPException(status_code=500, detail="Error processing agent request")
