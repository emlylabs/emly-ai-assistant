"""Wrapper around the per-process ``ConversationSessionManager``.

Phase 3: built lazily on first use with bounded retry. Constructing the
session manager touches the LLM client + the Qdrant client; a single
boot blip on either should not crash the worker into a Kubernetes
restart loop.
"""
from __future__ import annotations

import logging
import time
import traceback
from typing import Any, Optional

from agents.conversation_agent import ConversationSessionManager, setup_agent

logger = logging.getLogger(__name__)


_LAZY_RETRY_BACKOFF = (1, 2, 5, 10)  # seconds between init retries


class AgentNotAvailable(RuntimeError):
    """Agent could not be invoked (init failed or no topics configured).

    Raised by ``process_message`` so callers can decide whether to
    expose a generic operator-friendly fallback to the end user
    instead of forwarding the internal diagnostic verbatim.
    """


class AgentService:
    """Service for handling agent v2 conversations.

    The session manager is built on first ``ensure_initialized`` /
    ``process_message`` / ``get_flow_graph`` call. Failures are retried
    a bounded number of times with exponential-ish backoff; persistent
    failure leaves ``self.session_manager = None`` and ``is_available()``
    returns ``False`` so the chat surface returns 503 cleanly.
    """

    def __init__(self):
        self.session_manager: Optional[ConversationSessionManager] = None
        self._init_attempts = 0

    def ensure_initialized(self) -> bool:
        if self.session_manager is not None:
            return True

        for attempt, delay in enumerate(_LAZY_RETRY_BACKOFF, start=1):
            try:
                self.session_manager = setup_agent()
                if self.session_manager:
                    logger.info("ConversationSessionManager initialized (attempt %d)", attempt)
                    return True
            except Exception:
                logger.exception("Init attempt %d failed; retrying in %ds", attempt, delay)
                time.sleep(delay)
        logger.error("Failed to initialize ConversationSessionManager after %d attempts", len(_LAZY_RETRY_BACKOFF))
        self._init_attempts += 1
        return False

    def invalidate_bot(self, bot_id: str) -> None:
        if self.session_manager is None:
            return
        try:
            self.session_manager.invalidate_bot(bot_id)
        except Exception:
            logger.exception("invalidate_bot(%s) failed", bot_id)

    def process_message(
        self,
        bot_id: str,
        user_id: str,
        session_id: str,
        page_id: str,
        message: str,
        stream: bool = False,
        # Phase 3 backend-backfill: the dispatcher (and widget chat
        # ingress) resolves the source `BotChannel.id` and threads it
        # through here so message persistence can record per-message
        # channel attribution. `None` is acceptable for legacy callers
        # that don't know the channel — the column is nullable.
        channel_id: Optional[str] = None,
    ) -> Any:
        # Init failure is operator-facing, not user-facing. Raise so the
        # caller (legacy chat route, channel dispatcher) can render a
        # generic "Sorry, something went wrong" without leaking deploy
        # diagnostics to end users on Slack/Telegram/etc.
        if not self.ensure_initialized() or self.session_manager is None:
            raise AgentNotAvailable("agent service is not available")

        # Phase 10 backend-backfill: when an admin has taken over the
        # session, the bot stays silent. Persist the user's incoming turn
        # so it shows up in the admin's thread view, but skip the LLM
        # call. The widget receives an empty reply (the admin will type
        # the response via /sessions/{id}/reply, which the realtime
        # pub/sub propagates).
        try:
            from models.emly_sessions import EMLYSessions
            if EMLYSessions.is_taken_over(bot_id, session_id):
                from models.emly_messages import EMLYMessages
                EMLYMessages.insert_new_message(
                    bot_id=bot_id,
                    user_id=user_id,
                    session_id=session_id,
                    role="user",
                    message=message,
                    not_useful=False,
                    expanded_query=None,
                    page=page_id,
                    channel_id=channel_id,
                )
                if stream:
                    def takeover_stream():
                        # Yield no tokens; the citations marker keeps the
                        # widget happy but signals "no bot response".
                        yield {"type": "citations", "data": [], "message_id": None}
                    return takeover_stream()
                return ("", [])
        except Exception:
            logger.debug("takeover gating skipped (non-fatal)", exc_info=True)

        agent_handler = self.session_manager.get_handler(bot_id, user_id, session_id)

        if not len(agent_handler.topics) >= 1:
            if stream:
                def stream_generator():
                    yield {"type": "token", "data": "Please create at least one topic to get started. Click on Topics tab in Bot dashboard."}
                    yield {"type": "citations", "data": [], "message_id": None}
                return stream_generator()
            # No-topics is configuration-shaped: tell the user the bot
            # isn't set up. We deliberately keep this user-facing — it
            # isn't an internal failure, it's a "the human admin needs
            # to do something" message that's fine to surface.
            return ("Please create at least one topic to get started. Click on Topics tab in Bot dashboard.", [])

        response = agent_handler.process_user_input(message, stream, page_id, channel_id=channel_id)
        logger.info("Processed message for user '%s', session %s: %s...", user_id, session_id, message[:50])
        return response

    def is_available(self) -> bool:
        # Trigger the lazy build on first call. Subsequent calls are
        # cheap — ``ensure_initialized`` short-circuits when the
        # registry is already up. Without this, the chat route's
        # ``is_available()`` guard returns False forever (the registry
        # never initializes) and every chat request returns 503.
        return self.ensure_initialized() and self.session_manager is not None

    def get_flow_graph(self, bot_id: str) -> Optional[bytes]:
        if not self.ensure_initialized() or self.session_manager is None:
            logger.error("Session manager is not initialized; cannot generate workflow graph.")
            return None

        try:
            agent_handler = self.session_manager.get_handler(bot_id, "graph_user", "graph_session")
            import os
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_path = temp_file.name
            try:
                agent_handler.workflow.get_graph().draw_mermaid_png(output_file_path=temp_path)
                with open(temp_path, "rb") as f:
                    image_data = f.read()
                logger.info("Workflow graph generated successfully")
                return image_data
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
        except Exception:
            logger.exception("Could not generate workflow graph")
            return None
