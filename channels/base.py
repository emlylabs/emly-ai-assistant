"""Channel adapter interface.

Every chat surface (web widget, Slack, Teams, Google Chat, WhatsApp,
Telegram, …) implements this interface. The runtime dispatcher in
``channels/dispatcher.py`` is the single entry point — it delegates
inbound parsing, signature verification, and outbound delivery to the
adapter, while owning idempotency, identity hashing, retries, and
backgrounding.

Inbound flow (`channels/dispatcher.py`):
    1. ``handle_handshake(req, secrets)`` (GET-only paths) — return
       challenge response or ``None``.
    2. ``verify_signature(req, secrets)`` — fail closed.
    3. ``extract_event_id(req)`` → two-phase dedupe claim.
    4. ``parse_inbound(req, secrets)`` → ``IncomingMessage`` or ``None``.
    5. ``is_self(secrets, raw)`` filter — drop our own posts.
    6. Dispatcher resolves emly identity, runs agent, then either
       returns ``format_sync_reply(out, reply_handle)`` (sync mode) or
       schedules a background task that calls ``send(channel, handle, out)``.

Outbound flow:
    Adapters never touch ``BotChannel.credentials_encrypted`` directly —
    they call ``self.auth.get_access_token(channel)`` so refresh-on-demand
    and rotation are invisible to the per-platform code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Awaitable, Literal, Optional, Set

from channels.contracts import ChannelCaps, ChatType, IncomingMessage, OutgoingMessage

if TYPE_CHECKING:
    from channels.auth.base import AuthStrategy, InstallMetadata
    from models.bot_channels import BotChannelModel
    from pydantic import BaseModel


ReplyMode = Literal["sync", "async"]


class InstallError(Exception):
    """Raised when an adapter cannot complete a fresh install — e.g.
    Telegram refuses the webhook URL, Teams credentials don't mint an
    AAD token, Meta token upgrade fails. The admin CRUD layer catches
    this, rolls back the partially-created ``BotChannel`` row, and
    surfaces ``str(exc)`` to the operator as a 4xx error."""


class ChannelAdapter(ABC):
    """Base class for all channel adapters."""

    type: str = ""
    auth: "AuthStrategy"
    install_addressing: Literal["by_path", "by_payload"] = "by_path"
    default_reply_mode: ReplyMode = "async"
    supported_reply_modes: Set[ReplyMode] = {"async"}
    chat_types_supported: Set[ChatType] = {"dm"}
    capabilities: ChannelCaps = ChannelCaps()
    # When set on a sync-mode adapter, the dispatcher races the agent
    # against this timeout (seconds). If the agent finishes in time, the
    # reply is returned inline; if not, the dispatcher acks the webhook
    # and lets the agent finish in the background, posting via ``send``.
    # ``None`` keeps the legacy "wait forever inside the request" behavior.
    sync_response_timeout: Optional[float] = None
    # Body the dispatcher returns when ack'ing an async webhook. Some
    # platforms (Google Chat) treat unknown JSON as a malformed reply
    # and surface "App isn't responding" — those override to ``{}``.
    async_ack_body: Any = {"status": "accepted"}

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------
    @abstractmethod
    async def verify_signature(self, request: Any, secrets: "BaseModel") -> bool:
        """Reject anything that didn't come from the expected platform."""

    async def handle_handshake(self, request: Any, secrets: Optional["BaseModel"]) -> Optional[Any]:
        """Return an early response (e.g. WhatsApp ``hub.challenge``,
        Slack ``url_verification``) or ``None`` to fall through. Called
        before ``verify_signature`` since handshakes typically have no
        signature; adapter is responsible for handshake-internal auth.
        """
        return None

    @abstractmethod
    async def parse_inbound(self, request: Any, secrets: "BaseModel") -> Optional[IncomingMessage]:
        """Parse a webhook payload. Return ``None`` to ignore."""

    def extract_install_key(self, request: Any) -> Optional[str]:
        """For ``by_payload`` adapters, return the install identifier
        from the payload (Slack ``team_id``, Teams ``tenant_id``).
        Default: ``None`` (used by ``by_path`` adapters)."""
        return None

    def extract_event_id(self, request: Any) -> Optional[str]:
        """Stable id for retry dedupe. Return ``None`` to skip dedupe."""
        return None

    def is_self(self, secrets: "BaseModel", raw_payload: dict) -> bool:
        """True if the inbound is the bot's own post (drop)."""
        return False

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------
    def format_sync_reply(self, out: OutgoingMessage, reply_handle: Any) -> Any:
        """For sync-reply adapters: build the HTTP response body that
        carries the bot's reply inline."""
        raise NotImplementedError(f"{self.type} does not support sync replies")

    @abstractmethod
    async def send(
        self,
        channel: "BotChannelModel",
        reply_handle: Any,
        out: OutgoingMessage,
    ) -> None:
        """Deliver the bot's response back via the platform's API."""

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------
    async def extract_install_metadata(self, secrets: "BaseModel") -> "InstallMetadata":
        """Called after the admin pastes static credentials (or after
        OAuth callback if the strategy delegates here). The adapter
        may call platform APIs (Telegram getMe, GChat parse SA email)
        to resolve the install identity / display name."""
        return await self.auth.extract_install_metadata(secrets)

    async def healthcheck(self, channel: "BotChannelModel") -> dict:
        """Return ``{ok: bool, info: {...}}`` for the admin dashboard.
        Default: token mint test only."""
        try:
            await self.auth.get_access_token(channel)
            return {"ok": True, "info": {"note": "token mint succeeded"}}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "info": {"error": str(e)}}

    def parse_retry_after(self, response) -> "Optional[float]":
        """Return seconds to wait before retrying an outbound 429/5xx,
        or ``None`` to fall back to dispatcher default backoff.

        Default reads the ``Retry-After`` header. Override for platforms
        that put it elsewhere — Telegram returns it in the JSON body's
        ``parameters.retry_after``.
        """
        h = response.headers.get("retry-after")
        if h:
            stripped = h.strip()
            try:
                return float(stripped)
            except ValueError:
                # Retry-After can also be an HTTP-date — we don't parse
                # that; let the dispatcher use its default backoff.
                return None
        return None

    async def rollback_install(
        self, channel: "BotChannelModel", secrets: "BaseModel"
    ) -> None:
        """Best-effort cleanup if a fresh install fails after partial
        platform-side state was created.

        Distinct from ``auth.revoke``: ``revoke`` runs against an active,
        fully-installed channel and assumes ``auth.get_access_token``
        works. ``rollback_install`` runs when an install never finished
        — credentials may not be persisted, the row may not be valid for
        token mint. Adapters override only when they push state to the
        platform during install (Telegram's ``setWebhook``).
        """
        return None
