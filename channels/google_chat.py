"""Google Chat channel adapter.

Service-account JWT auth: outbound calls mint a bearer via RS256-signed
assertion against Google's OAuth token endpoint. Inbound webhooks are
authenticated by a JWT signed by Google's chat-system service account.

Replaces the legacy ``/emly/google/webhook`` endpoint that hard-coded a
single bot via ``GOOGLE_CHAT_BOT_ID``. Each install is now a separate
``BotChannel`` row keyed by the service account email; the configured
webhook URL is stored on the install (and used as the JWT audience).
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import Request
from pydantic import BaseModel, Field

from channels.auth._http import make_client
from channels.auth.base import InstallMetadata
from channels.auth.service_account_jwt import ServiceAccountJWT
from channels.base import ChannelAdapter, InstallError
from channels.contracts import ChannelCaps, ChatType, IncomingMessage, OutgoingMessage
from channels.jwt_verifier import (
    GOOGLE_OIDC_ISSUER,
    GOOGLE_SYSTEM_SA,
    verify_google_chat_jwt,
)
from channels.registry import register
from models.bot_channels import BotChannelModel

log = logging.getLogger(__name__)

CHAT_API = "https://chat.googleapis.com/v1"


class GoogleChatSecrets(BaseModel):
    version: int = 1
    service_account_json: Dict[str, Any]
    service_account_email: str
    # Server-managed: stamped to the canonical
    # ``{PUBLIC_BASE_URL}/channels/google_chat/{channel_id}/events`` after the
    # row is created, then used as the JWT audience for inbound verification.
    # Operators paste the same value into the Chat app config; nothing to type
    # at install time.
    webhook_url: str = ""


class GoogleChatServiceAccount(ServiceAccountJWT):
    secrets_model = GoogleChatSecrets

    def scopes(self) -> List[str]:
        return ["https://www.googleapis.com/auth/chat.bot"]

    def service_account_dict(self, secrets: BaseModel) -> dict:
        assert isinstance(secrets, GoogleChatSecrets)
        return secrets.service_account_json


class GoogleChatAdapter(ChannelAdapter):
    type = "google_chat"
    auth = GoogleChatServiceAccount()
    install_addressing = "by_path"
    # Chat shows "App isn't responding" when the webhook returns a body
    # it doesn't recognize as a Message — empty ``{}`` is the documented
    # async ack, and we prefer sync replies when the agent is fast enough
    # to land inside Chat's 30s webhook window. The dispatcher's
    # ``sync_response_timeout`` fallback still acks asynchronously if the
    # agent overruns, so slow queries don't lose their reply.
    default_reply_mode = "sync"
    supported_reply_modes = {"sync", "async"}
    sync_response_timeout = 25.0
    async_ack_body: Dict[str, Any] = {}
    chat_types_supported = {"dm", "thread"}
    capabilities = ChannelCaps(
        supports_threading=True,
        max_message_length=4000,
    )

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------
    async def verify_signature(self, request: Request, secrets: GoogleChatSecrets) -> bool:
        bearer = request.headers.get("authorization", "")
        if not bearer.lower().startswith("bearer "):
            return False
        token = bearer[7:].strip()
        # Google Chat may sign with the system SA, the app's own SA, or
        # Google's OIDC issuer (``https://accounts.google.com``), depending on
        # the Chat app's auth-audience config. Accept all three — the verifier
        # picks the cert/JWKS source from the (unverified) iss claim and then
        # enforces a strict iss/aud/sig check.
        allowed_issuers = [
            GOOGLE_SYSTEM_SA,
            GOOGLE_OIDC_ISSUER,
            secrets.service_account_email,
        ]
        try:
            await verify_google_chat_jwt(
                token,
                audience=secrets.webhook_url,
                allowed_issuers=allowed_issuers,
            )
            return True
        except PermissionError as e:
            log.warning("Google Chat JWT verify failed: %s", e)
            return False

    def extract_event_id(self, request: Request) -> Optional[str]:
        body = _peek(request)
        if not body:
            return None
        # Google Chat events lack a stable retry id; hash the
        # high-cardinality fields. Status events ("ADDED_TO_SPACE",
        # "REMOVED_FROM_SPACE") happen rarely and are idempotent — we
        # let the dispatcher dedupe them too.
        msg = _extract_message(body) or {}
        space = (body.get("space") or msg.get("space") or _extract_chat_space(body) or {})
        sender = (msg.get("sender") or body.get("user") or {})
        ts = msg.get("createTime") or body.get("eventTime") or ""
        seed = f"{ts}:{space.get('name', '')}:{sender.get('name', '')}:{msg.get('name','')}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16] if seed.strip(":") else None

    async def parse_inbound(self, request: Request, secrets: GoogleChatSecrets) -> Optional[IncomingMessage]:
        body = _peek(request) or {}
        # v2 envelope: ``chat.messagePayload.message``. v1 envelope:
        # ``type=MESSAGE``, ``message`` at top level.
        envelope = "v2" if body.get("chat") else "v1"
        msg = _extract_message(body)
        if msg is None:
            return None
        sender = msg.get("sender") or {}
        if sender.get("type") == "BOT":
            return None
        text = msg.get("argumentText") or msg.get("text") or ""
        text = text.strip()
        if not text:
            return None
        space = msg.get("space") or _extract_chat_space(body) or {}
        thread = msg.get("thread") or {}
        chat_type: ChatType
        if thread.get("name"):
            chat_type = "thread"
        else:
            chat_type = "dm"
        user_external = sender.get("email") or sender.get("name") or ""
        if not user_external:
            return None
        session_external = thread.get("name") or f"gchat-{space.get('name','')}-{user_external}"
        return IncomingMessage(
            channel_id="",
            user_external_id=user_external,
            session_external_id=session_external,
            text=text,
            chat_type=chat_type,
            raw_payload=body,
            reply_handle={
                "space_name": space.get("name"),
                "thread_name": thread.get("name"),
                "envelope": envelope,
            },
        )

    def is_self(self, secrets: GoogleChatSecrets, raw_payload: dict) -> bool:
        msg = _extract_message(raw_payload) or {}
        sender = msg.get("sender") or {}
        return sender.get("type") == "BOT" and sender.get("email") == secrets.service_account_email

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------
    def format_sync_reply(self, out: OutgoingMessage, reply_handle: Any) -> Any:
        msg: Dict[str, Any] = {"text": out.text}
        handle = reply_handle if isinstance(reply_handle, dict) else {}
        thread_name = handle.get("thread_name")
        if thread_name:
            msg["thread"] = {"name": thread_name}
        # Match the inbound envelope: v1 apps expect a plain ``Message``
        # object, v2 (Chat App SDK) apps expect the ``hostAppDataAction``
        # wrapper. Sending the wrong shape gets silently dropped by Chat
        # and surfaces as "App isn't responding" to the user.
        if handle.get("envelope") == "v2":
            return {
                "hostAppDataAction": {
                    "chatDataAction": {
                        "createMessageAction": {"message": msg}
                    }
                }
            }
        return msg

    async def send(self, channel: BotChannelModel, reply_handle: Any, out: OutgoingMessage) -> None:
        token = await self.auth.get_access_token(channel)
        space_name = reply_handle.get("space_name")
        if not space_name:
            log.warning("Google Chat send missing space_name")
            return
        body: Dict[str, Any] = {"text": out.text}
        thread_name = reply_handle.get("thread_name")
        if thread_name:
            body["thread"] = {"name": thread_name}
        async with make_client() as client:
            resp = await client.post(
                f"{CHAT_API}/{space_name}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def extract_install_metadata(self, secrets: GoogleChatSecrets) -> InstallMetadata:
        # ``webhook_url`` is server-managed (stamped after the row is created),
        # so we don't validate it here — only the operator-supplied fields.
        if not secrets.service_account_email:
            raise ValueError("service_account_email required")
        return InstallMetadata(
            external_id=secrets.service_account_email,
            display_name=secrets.service_account_email,
        )

    async def post_create_install(self, channel: BotChannelModel, secrets: GoogleChatSecrets) -> None:
        # Validate that we can actually mint a bearer with this SA.
        try:
            await self.auth.get_access_token(channel)
        except Exception as e:
            raise InstallError(
                f"Service account token mint failed — check the JSON key and ensure the "
                f"Chat API is enabled in the project: {e}"
            )

    async def healthcheck(self, channel: BotChannelModel) -> dict:
        try:
            token = await self.auth.get_access_token(channel)
            async with make_client() as client:
                resp = await client.get(
                    f"{CHAT_API}/spaces",
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                spaces = resp.json().get("spaces") or []
            return {"ok": True, "info": {"space_count": len(spaces)}}
        except Exception as e:
            return {"ok": False, "info": {"error": str(e)}}


def _peek(request: Request) -> Optional[dict]:
    body = getattr(request, "_body", None)
    if body is None:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def _extract_message(body: dict) -> Optional[dict]:
    """Tolerant message extractor. Old format puts ``message`` at top
    level when ``type == "MESSAGE"``. v2 nests under
    ``chat.messagePayload.message``."""
    if body.get("type") == "MESSAGE":
        return body.get("message") or {}
    chat = body.get("chat") or {}
    payload = chat.get("messagePayload") or {}
    if payload.get("message"):
        return payload["message"]
    return None


def _extract_chat_space(body: dict) -> Optional[dict]:
    chat = body.get("chat") or {}
    payload = chat.get("messagePayload") or {}
    return payload.get("space") or None


register(GoogleChatAdapter())
