"""Embed widget channel adapter.

The web widget is dispatcher-bypassed: the FastAPI route in
``routes/chat.py`` and ``routes/widget.py`` handle the request directly
because the body is already in canonical ``AgentRequest`` shape. The
adapter exists to satisfy the contract (so type-checks pass) and to
advertise capabilities — adding Slack/Telegram is a new file on the
same interface, not a refactor of the runtime.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from channels.base import ChannelAdapter
from channels.contracts import ChannelCaps, IncomingMessage, OutgoingMessage


class _WebWidgetNoAuth:
    """The widget speaks to our own embed snippet over HTTPS to a
    path-scoped URL. There is no platform signature to validate and no
    token to mint — define a minimal stub that satisfies the
    ``AuthStrategy`` shape without the OAuth/static behavior."""

    requires_oauth_callback = False
    allows_direct_static_install = True
    secrets_model = BaseModel

    def validate_secrets(self, payload: dict):
        return BaseModel()

    async def get_access_token(self, channel) -> str:
        return ""


class WebWidgetAdapter(ChannelAdapter):
    type = "web_widget"
    auth = _WebWidgetNoAuth()  # type: ignore[assignment]
    install_addressing = "by_path"
    default_reply_mode = "sync"
    supported_reply_modes = {"sync"}
    chat_types_supported = {"dm"}
    capabilities = ChannelCaps(
        supports_streaming=True,
        supports_threading=False,
        supports_edit_after_post=False,
        supports_attachments=False,
        supports_rich_blocks=False,
    )

    async def verify_signature(self, request: Any, secrets) -> bool:
        return True

    async def parse_inbound(self, request: Any, secrets) -> Optional[IncomingMessage]:
        return None

    async def send(self, channel, reply_handle, out: OutgoingMessage) -> None:
        raise NotImplementedError(
            "Web widget responses are written inline by the route handler."
        )


WEB_WIDGET = WebWidgetAdapter()
