"""Phase 9 backend-backfill — SSE endpoint for the live conversation feed.

The admin UI's Conversations list opens an EventSource connection here
and merges incoming events (new turn, session resolved, rating) into
its in-memory session list without re-fetching.

Single-replica only — the in-process pub/sub in `services.realtime`
doesn't span workers. When `WEB_CONCURRENCY > 1`, this endpoint
returns 503 with `X-Reason: multi-replica-not-supported` so the UI
falls back to its existing 5-second poll.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from models.admin_bot_memberships import AdminBotMemberships
from models.admin_users import AdminUserModel
from models.bots import Bots
from services.auth.dependencies import get_admin
from services.realtime import publish, subscribe, unsubscribe

log = logging.getLogger(__name__)
router = APIRouter()


def _multi_worker() -> bool:
    try:
        return int(os.environ.get("WEB_CONCURRENCY", "1") or "1") > 1
    except (TypeError, ValueError):
        return False


@router.get("/bots/{slug}/conversations/stream")
async def stream_conversations(
    slug: str,
    request: Request,
    admin: AdminUserModel = Depends(get_admin),
):
    """SSE stream of session-level events for the admin UI.

    Event shapes (newline-delimited, JSON-after-data prefix):
        - ``session_activity``  — a new message landed; payload includes
          session_id and bot_id.
        - ``session_resolved``  — admin marked the session resolved.
        - ``rating``            — end-user rated an assistant message.
    """
    if _multi_worker():
        # The pub/sub is in-process; refusing here is friendlier than
        # silently dropping events.
        return StreamingResponse(
            iter(()),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"X-Reason": "multi-replica-not-supported"},
        )

    bot = Bots.get_by_slug(slug)
    if bot is None or bot.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    if AdminBotMemberships.get(admin.id, bot.id) is None and not admin.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this bot")

    queue = subscribe(bot.id)

    async def event_stream() -> AsyncIterator[bytes]:
        last_heartbeat = time.time()
        try:
            yield b": ok\n\n"  # initial keepalive comment
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Block briefly then yield control so disconnect
                    # detection stays responsive. asyncio.to_thread
                    # avoids tying up the event loop on the blocking
                    # `Queue.get`.
                    event = await asyncio.wait_for(
                        asyncio.to_thread(queue.get, True, 1.0),
                        timeout=1.5,
                    )
                except (asyncio.TimeoutError, Exception):
                    event = None
                now = time.time()
                if event is not None:
                    # Standard SSE framing: `data: <json>\n\n`
                    yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
                    last_heartbeat = now
                elif now - last_heartbeat > 30:
                    yield b": keepalive\n\n"
                    last_heartbeat = now
        finally:
            unsubscribe(bot.id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx response buffering
        },
    )


# Helper exported for other write paths (Phase 6 resolve, Phase 7 rating,
# Phase 10 takeover) to publish events without re-importing the pub/sub.
def emit_event(bot_id: str, event_type: str, **payload) -> None:
    publish(bot_id, {"type": event_type, "bot_id": bot_id, **payload})
