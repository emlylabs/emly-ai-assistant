"""Telegram channel adapter.

Static-token install. Admin pastes the bot token from @BotFather; we
call ``getMe`` to learn the bot's id and username (the install
identity), then call ``setWebhook`` ourselves so the operator doesn't
have to. A randomly generated ``secret_token`` is stored alongside the
bot token and pushed into ``setWebhook``; Telegram echoes it back in
the ``X-Telegram-Bot-Api-Secret-Token`` header on every webhook.

Default reply mode is async (the dispatcher acks 200 fast and posts
the reply via ``sendMessage``). Sync mode is supported and lets the
adapter return the reply inline as a ``sendMessage`` method JSON.
"""
from __future__ import annotations

import logging
import os
import secrets as py_secrets
from typing import Any, Optional

import httpx
from fastapi import Request
from pydantic import BaseModel, Field

from channels.auth._http import make_client
from channels.auth.base import InstallMetadata
from channels.auth.static_token import StaticToken
from channels.base import ChannelAdapter, InstallError
from channels.contracts import ChannelCaps, ChatType, IncomingMessage, OutgoingMessage
from channels.registry import register
from models.bot_channels import BotChannelModel

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class TelegramSecrets(BaseModel):
    version: int = 1
    bot_token: str
    secret_token: str = ""
    bot_id: int = 0
    bot_username: str = ""


class _TelegramAuth(StaticToken):
    """StaticToken specialized to read ``bot_token`` instead of the
    default ``access_token`` field."""

    def __init__(self):
        super().__init__(secrets_model=TelegramSecrets, token_field="bot_token")


class TelegramAdapter(ChannelAdapter):
    type = "telegram"
    auth = _TelegramAuth()
    install_addressing = "by_path"
    default_reply_mode = "async"
    supported_reply_modes = {"sync", "async"}
    chat_types_supported = {"dm", "group"}
    capabilities = ChannelCaps(
        supports_streaming=False,
        supports_threading=False,
        supports_attachments=False,
        supports_rich_blocks=False,
        max_message_length=4000,
    )

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------
    async def verify_signature(self, request: Request, secrets: TelegramSecrets) -> bool:
        if not secrets.secret_token:
            log.warning("Telegram channel has no secret_token configured — accepting anyway")
            return True
        provided = request.headers.get("x-telegram-bot-api-secret-token", "")
        return _ct_eq(provided, secrets.secret_token)

    async def parse_inbound(self, request: Request, secrets: TelegramSecrets) -> Optional[IncomingMessage]:
        body = await _request_json(request)
        if "message" not in body:
            return None
        msg = body["message"]
        sender = msg.get("from") or {}
        if sender.get("is_bot"):
            return None
        chat = msg.get("chat") or {}
        chat_type_raw = chat.get("type", "private")
        if chat_type_raw == "channel":
            return None
        text = msg.get("text") or msg.get("caption") or ""
        if not text:
            return None
        chat_type: ChatType
        if chat_type_raw == "private":
            chat_type = "dm"
        elif chat_type_raw in ("group", "supergroup"):
            chat_type = "group"
            mention_target = f"@{secrets.bot_username}".lower() if secrets.bot_username else None
            if not mention_target or mention_target not in text.lower():
                # Strict opt-in: only respond when explicitly mentioned in groups.
                return None
            # Strip the mention from the start of the text so the agent sees a clean prompt.
            text = _strip_mention(text, mention_target)
        else:
            return None
        return IncomingMessage(
            channel_id="",
            user_external_id=str(sender.get("id", "")),
            session_external_id=str(chat.get("id", "")),
            text=text,
            chat_type=chat_type,
            raw_payload=body,
            reply_handle={
                "chat_id": chat.get("id"),
                "reply_to_message_id": msg.get("message_id"),
            },
        )

    def extract_event_id(self, request: Request) -> Optional[str]:
        body = _peek_json(request)
        if not body:
            return None
        update_id = body.get("update_id")
        return str(update_id) if update_id is not None else None

    def is_self(self, secrets: TelegramSecrets, raw_payload: dict) -> bool:
        msg = raw_payload.get("message") or {}
        sender = msg.get("from") or {}
        return sender.get("id") == secrets.bot_id

    def parse_retry_after(self, response) -> Optional[float]:
        """Telegram puts retry hints in the JSON body, not the header —
        ``parameters.retry_after`` (seconds). Header Retry-After is only
        sent for 5xx (rare), so check the body first.
        """
        try:
            body = response.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            params = body.get("parameters") or {}
            ra = params.get("retry_after")
            if isinstance(ra, (int, float)) and ra > 0:
                return float(ra)
        return super().parse_retry_after(response)

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------
    def format_sync_reply(self, out: OutgoingMessage, reply_handle: Any) -> Any:
        return {
            "method": "sendMessage",
            "chat_id": reply_handle.get("chat_id"),
            "text": _escape_md_v2(out.text),
            "parse_mode": "MarkdownV2",
            "reply_to_message_id": reply_handle.get("reply_to_message_id"),
        }

    async def send(self, channel: BotChannelModel, reply_handle: Any, out: OutgoingMessage) -> None:
        bot_token = await self.auth.get_access_token(channel)
        async with make_client() as client:
            resp = await client.post(
                f"{TELEGRAM_API}/bot{bot_token}/sendMessage",
                json={
                    "chat_id": reply_handle.get("chat_id"),
                    "text": _escape_md_v2(out.text),
                    "parse_mode": "MarkdownV2",
                    "reply_to_message_id": reply_handle.get("reply_to_message_id"),
                },
            )
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def extract_install_metadata(self, secrets: TelegramSecrets) -> InstallMetadata:
        async with make_client() as client:
            resp = await client.get(f"{TELEGRAM_API}/bot{secrets.bot_token}/getMe")
            resp.raise_for_status()
            data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram getMe failed: {data}")
        result = data["result"]
        bot_id = int(result["id"])
        username = result.get("username", "")
        # Stamp the discovered identity onto the secrets object so
        # subsequent inbounds can verify is_self / mention without
        # another network call.
        secrets.bot_id = bot_id
        secrets.bot_username = username
        return InstallMetadata(
            external_id=str(bot_id),
            display_name=f"@{username}" if username else f"bot-{bot_id}",
        )

    async def post_create_install(self, channel: BotChannelModel, secrets: TelegramSecrets) -> None:
        """Auto-register webhook with Telegram and rotate the stored
        secrets to include the discovered ``bot_id`` / ``bot_username``
        and the fresh ``secret_token``."""
        from services.secrets import encrypt as fernet_encrypt
        from models.bot_channels import BotChannels

        if not secrets.secret_token:
            secrets.secret_token = py_secrets.token_urlsafe(32)
        if not secrets.bot_id or not secrets.bot_username:
            metadata = await self.extract_install_metadata(secrets)
            channel_id = channel.id
            BotChannels.update_external_id(channel_id, metadata.external_id, metadata.display_name)

        webhook_url = _public_webhook_url(channel)
        if not webhook_url.startswith("https://"):
            raise InstallError(
                "Telegram requires an HTTPS webhook URL. "
                f"PUBLIC_BASE_URL is currently '{webhook_url.split('/channels/')[0]}'. "
                "Set PUBLIC_BASE_URL to a publicly reachable HTTPS URL "
                "(ngrok / cloudflared for local dev, your production hostname otherwise) "
                "and try again."
            )
        async with make_client() as client:
            resp = await client.post(
                f"{TELEGRAM_API}/bot{secrets.bot_token}/setWebhook",
                json={
                    "url": webhook_url,
                    "secret_token": secrets.secret_token,
                    "drop_pending_updates": True,
                    "allowed_updates": ["message"],
                },
            )
            try:
                body = resp.json()
            except Exception:
                body = {}
            if resp.status_code >= 400 or not body.get("ok"):
                description = body.get("description") if isinstance(body, dict) else None
                raise InstallError(
                    f"Telegram setWebhook failed (status {resp.status_code}): "
                    f"{description or resp.text[:300] or 'no error description from Telegram'}"
                )

        BotChannels.update_credentials(channel.id, fernet_encrypt(secrets.model_dump_json()))

    async def healthcheck(self, channel: BotChannelModel) -> dict:
        secrets = _decrypt(channel)
        try:
            async with make_client() as client:
                resp = await client.get(f"{TELEGRAM_API}/bot{secrets.bot_token}/getMe")
                resp.raise_for_status()
                body = resp.json()
            return {"ok": bool(body.get("ok")), "info": body.get("result", {})}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "info": {"error": str(e)}}

    async def revoke(self, channel: BotChannelModel) -> None:
        try:
            secrets = _decrypt(channel)
        except Exception:
            return
        await self._delete_webhook(secrets.bot_token, channel.id)

    async def rollback_install(
        self, channel: BotChannelModel, secrets: TelegramSecrets
    ) -> None:
        """Install partially succeeded: ``setWebhook`` may have written
        our URL onto the bot, so detach it before the row is deleted —
        otherwise Telegram keeps retrying our defunct endpoint until
        the bot owner manually clears it via BotFather.
        """
        if secrets.bot_token:
            await self._delete_webhook(secrets.bot_token, channel.id)

    async def _delete_webhook(self, bot_token: str, channel_id: str) -> None:
        try:
            async with make_client() as client:
                await client.post(f"{TELEGRAM_API}/bot{bot_token}/deleteWebhook")
        except Exception:
            log.exception("deleteWebhook failed channel=%s", channel_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ct_eq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


_MD_V2_SPECIALS = r"_*[]()~`>#+-=|{}.!\\"


def _escape_md_v2(text: str) -> str:
    out = []
    for ch in text:
        if ch in _MD_V2_SPECIALS:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _strip_mention(text: str, mention: str) -> str:
    lowered = text.lower()
    idx = lowered.find(mention)
    if idx < 0:
        return text
    return (text[:idx] + text[idx + len(mention):]).strip()


def _public_webhook_url(channel: BotChannelModel) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
    return f"{base}/channels/telegram/{channel.id}/events"


async def _request_json(request: Request) -> dict:
    body = await request.body()
    if not body:
        return {}
    import json as _json
    return _json.loads(body.decode("utf-8"))


def _peek_json(request: Request) -> Optional[dict]:
    """Synchronous body peek — used in extract_event_id, which is sync.

    The dispatcher reads the body before calling us; FastAPI caches it
    on ``request._body``. Re-parsing is cheap.
    """
    body = getattr(request, "_body", None)
    if body is None:
        return None
    import json as _json
    try:
        return _json.loads(body.decode("utf-8"))
    except Exception:
        return None


def _decrypt(channel: BotChannelModel) -> TelegramSecrets:
    from channels.dispatcher import decrypt_secrets
    return decrypt_secrets(channel)  # type: ignore[return-value]


# Self-register at import time.
register(TelegramAdapter())
