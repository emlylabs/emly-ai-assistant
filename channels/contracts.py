"""Canonical message types for channel adapters.

The chat surface is bot-many, channel-many. Embed widget today; Slack /
Teams / Google Chat / WhatsApp / Telegram tomorrow. Each channel speaks
a different wire format, but the runtime only needs to see
``IncomingMessage`` / ``OutgoingMessage`` — the per-channel adapter is
responsible for translating the platform's payload to and from these
shapes.

Capability flags let the runtime (and the channel adapter) negotiate
features that aren't universal: web widgets stream over HTTP chunked,
Slack only supports edit-after-post for partial streaming, Google Chat
neither.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional


@dataclass
class ChannelCaps:
    """What a channel can / cannot do."""

    supports_streaming: bool = False
    supports_threading: bool = False
    supports_edit_after_post: bool = False
    supports_attachments: bool = False
    supports_rich_blocks: bool = False
    max_message_length: Optional[int] = None


# Where in the conversation hierarchy the inbound message lives. The
# dispatcher uses this to decide if the bot should respond at all
# (group chats are usually mention-only) and adapters use it to thread
# replies correctly.
ChatType = Literal["dm", "group", "channel", "thread"]


@dataclass
class IncomingMessage:
    """Adapter-normalized inbound message.

    ``user_external_id`` / ``session_external_id`` are platform ids;
    the dispatcher hashes them with ``(bot_id, channel.external_id)``
    to derive the stable emly ids.

    ``reply_handle`` is opaque per-adapter and carries everything the
    adapter needs to send a reply later (Slack: ``{channel, thread_ts}``;
    Teams: ``{service_url, conversation_id, activity_id}``; Telegram:
    ``{chat_id, reply_to_message_id}``; WhatsApp:
    ``{phone_number_id, to, context_message_id}``; Google Chat:
    ``{space, thread}``).

    ``kind`` distinguishes message types the dispatcher routes
    differently. ``"text"`` runs the agent. ``"canned_reply"`` skips
    the agent and ships ``canned_reply_text`` directly through the
    outbound retry/redaction pipeline — used by adapters (e.g.
    WhatsApp) that need to politely tell the user "I can only handle
    text" without involving the LLM.
    """

    channel_id: str
    user_external_id: str
    session_external_id: str
    text: str
    chat_type: ChatType = "dm"
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    received_at: datetime = field(default_factory=datetime.utcnow)
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    reply_handle: Any = None
    kind: Literal["text", "canned_reply"] = "text"
    canned_reply_text: str = ""


@dataclass
class OutgoingMessage:
    text: str
    citations: List[Dict[str, Any]] = field(default_factory=list)
    format_hints: Dict[str, Any] = field(default_factory=dict)
