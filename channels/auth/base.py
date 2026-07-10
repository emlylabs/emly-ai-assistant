"""AuthStrategy interface.

Every strategy declares (a) the secrets schema it accepts, (b) whether
it terminates an OAuth `/install` redirect, (c) how to mint an access
token from a stored row, (d) how to extract install identity / display
name on first contact, and (e) optionally how to revoke.

Adapters never touch ``BotChannel.credentials_encrypted`` directly —
they go through ``auth.get_access_token(channel)`` so refresh-on-demand
and atomic persist live in one place.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Type

from pydantic import BaseModel

if TYPE_CHECKING:
    from models.bot_channels import BotChannelModel


@dataclass
class InstallMetadata:
    """Returned after install (OAuth callback or static-secret create).

    ``external_id`` is the platform-stable install id stored on
    ``BotChannel.external_id`` (Slack ``team_id``, Telegram bot id,
    WhatsApp phone_number_id, …). ``display_name`` is operator-facing.
    """

    external_id: str
    display_name: str
    granted_scopes: List[str] = field(default_factory=list)


class AuthStrategy(ABC):
    """Base class for all per-platform auth strategies."""

    secrets_model: Type[BaseModel]
    requires_oauth_callback: bool = False
    # Some OAuth-shaped strategies (Meta) also support pasting a
    # long-lived static token directly. Static-only strategies leave
    # this False; the admin CRUD endpoint reads it to decide whether
    # to expose the form path for an OAuth-capable adapter.
    allows_direct_static_install: bool = True

    def validate_secrets(self, payload: dict) -> BaseModel:
        return self.secrets_model.model_validate(payload)

    @abstractmethod
    async def get_access_token(self, channel: "BotChannelModel") -> str:
        """Return a usable bearer token for outbound calls. Refreshes on
        demand and persists the rotated token; adapters call this every
        time, not once."""

    async def extract_install_metadata(self, secrets: BaseModel) -> InstallMetadata:
        """Default: subclasses must implement; static strategies often
        delegate to the adapter (e.g. Telegram calls getMe)."""
        raise NotImplementedError

    async def revoke(self, channel: "BotChannelModel") -> None:
        """Best-effort platform-side uninstall. Default: no-op."""
        return None
