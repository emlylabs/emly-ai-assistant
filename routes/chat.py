import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from utils.schemas import AgentRequest
from utils.dependencies import get_agent_service
from services.agent_service import AgentService
from fastapi.responses import StreamingResponse

router = APIRouter()

logger = logging.getLogger(__name__)


@router.post("/api/chat", summary="Process agent request")
async def process_agent_request(
        request: AgentRequest,
        agent_service: AgentService = Depends(get_agent_service),
        bot_id: Optional[str] = None,
):
    """
    Process a request using the agent v2 framework
    Returns:
    - AgentResponse with the agent's response
    """
    user_id = request.user_id
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    if not request.messages:
        raise HTTPException(status_code=400, detail="Message is required")
    if not request.timestamp:
        raise HTTPException(status_code=400, detail="Timestamp is required")

    # Path-scoped widget callers pass `bot_id` directly; the legacy
    # `/emly/api/chat` route receives it via `X-Emly-BotID` (CustomMiddleware
    # materializes it onto the request body).
    bot_id = bot_id or getattr(request, "bot_id", None)
    if not bot_id:
        raise HTTPException(status_code=400, detail="Bot ID is required")

    try:
        if not agent_service.is_available():
            raise HTTPException(
                status_code=503,
                detail="Agent service is not available. Please check the configuration and ensure OPENAI_API_KEY is set."
            )

        # Phase 3 backend-backfill: the legacy widget chat ingress doesn't
        # carry an explicit channel_id, but messages still arrive from the
        # web_widget channel kind. Look up the bot's default web_widget
        # BotChannel row so message persistence can record per-message
        # channel attribution. Falls back to None when the bot hasn't
        # installed a web_widget channel — `EMLYMessage.channel_id` is
        # nullable.
        from models.bot_channels import BotChannels
        channel_id = BotChannels.get_default_web_widget_channel_id(bot_id)

        response = agent_service.process_message(
            bot_id=bot_id,
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
                yield json.dumps({"message": {"role": "assistant", "content": response_text}, "citations": [], "done": False, "done_reason": None}) + "\n"
                yield json.dumps({"message": {"role": "assistant", "content": ""}, "citations": citations, "done": True, "done_reason": "stop", "message_id": 123}) + "\n"
            return StreamingResponse(stream_non_stream())
        else:
            def stream_generator():
                for event in response:
                    if event["type"] == "token":
                        yield json.dumps({
                            "message": {"role": "assistant", "content": event["data"]},
                            "citations": [],
                            "done": False,
                            "done_reason": None
                        }) + "\n"

                    elif event["type"] == "citations":
                        yield json.dumps({
                            "message": {"role": "assistant", "content": ""},
                            "citations": event["data"],
                            "done": True,
                            "done_reason": "stop",
                            "message_id": event.get("message_id", None)
                        }) + "\n"

            return StreamingResponse(stream_generator())

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error processing agent request")
        raise HTTPException(status_code=500, detail="Error processing agent request")


@router.post("/widget/{bot_slug}/chat", summary="Web widget chat (path-scoped to bot)")
async def widget_chat(
    bot_slug: str,
    request: AgentRequest,
    http_request: Request,
    agent_service: AgentService = Depends(get_agent_service),
):
    """Path-based chat surface for the embed widget (Phase 4).

    The bot is identified by ``{bot_slug}`` in the URL — the widget
    snippet bakes this in once at generation time. Returns 404 if the
    bot doesn't exist or is soft-deleted; otherwise delegates to the
    same agent pipeline as the legacy ``/emly/api/chat`` route.
    """
    # Use the shared widget-route resolver so id/slug lookup order
    # matches `routes.widget`. Same string always resolves to the same
    # bot whether the caller hits /chat, /config, or /action.
    from routes.widget import resolve_bot
    bot = resolve_bot(bot_slug)

    # Auto-create the end-user row scoped to this bot so admin views
    # (Bot users tab, Conversations tab) have a row to render against.
    # Legacy `/emly/api/chat` does this via `CustomMiddleware`; the
    # widget path goes around the middleware shortcut, so we do it here.
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
            # Race: a concurrent first message from the same end user
            # can hit ``insert_new_user`` after our pre-check. Re-read
            # to confirm the row exists; only escalate if it really
            # didn't get inserted.
            if EMLYUsers.get_user_by_id(bot.id, end_user_id) is None:
                logger.exception("Failed to auto-create end-user for bot=%s user=%s", bot.id, end_user_id)
            else:
                logger.info("Concurrent insert for bot=%s user=%s — using existing row", bot.id, end_user_id)

    return await process_agent_request(
        request=request,
        agent_service=agent_service,
        bot_id=bot.id,
    )


@router.get("/flow", summary="Get workflow graph")
async def get_flow_graph(
        agent_service: AgentService = Depends(get_agent_service)
):
    """
    Get the workflow graph as PNG image data
    Returns:
    - PNG image data
    """
    try:
        if not agent_service.is_available():
            raise HTTPException(
                status_code=503,
                detail="Agent service is not available. Please check the configuration and ensure OPENAI_API_KEY is set."
            )

        image_data = agent_service.get_flow_graph()
        if image_data is None:
            raise HTTPException(status_code=500, detail="Failed to generate workflow graph")

        from fastapi.responses import Response
        return Response(content=image_data, media_type="image/png")

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error generating workflow graph")
        raise HTTPException(status_code=500, detail="Error generating workflow graph")

