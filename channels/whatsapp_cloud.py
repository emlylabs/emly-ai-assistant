"""WhatsApp Cloud (Meta) channel adapter.

Two install paths:

1. **OAuth (Embedded Signup)** — Meta's OAuth dance returns a short-lived
   user token. ``MetaEmbeddedSignup.post_install_hook`` exchanges it
   for a long-lived (60-day) token and discovers the ``waba_id`` /
   ``phone_number_id``. Admin still needs to provide
   ``verify_token`` (used in the GET handshake) — they do that on a
   follow-up screen after callback. (For v1 the OAuth path stores
   whatever the callback can derive; admin completes the row via
   PUT /secrets afterwards.)

2. **Static** — admin pastes a long-lived system-user token from Meta
   Business Manager along with ``phone_number_id`` / ``waba_id`` /
   ``verify_token`` / ``display_phone_number`` directly. This is the
   primary v1 path because the OAuth dance requires a Facebook Login
   for Business app review — most operators reach for system-user
   tokens instead.

App-level secret ``META_APP_SECRET`` is read from env (one Meta app
per deployment); the per-install secrets blob carries the long-lived
token and the WABA/phone-number identity.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, List, Optional

from fastapi import Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from channels.auth._http import make_client
from channels.auth.base import InstallMetadata
from channels.auth.oauth2_auth_code import OAuth2AuthCodeBase, TokenSet
from channels.base import ChannelAdapter, InstallError
from channels.contracts import ChannelCaps, ChatType, IncomingMessage, OutgoingMessage
from channels.registry import register
from models.bot_channels import BotChannelModel

log = logging.getLogger(__name__)

_GRAPH_API_VERSION = os.environ.get("META_GRAPH_API_VERSION", "v21.0")
GRAPH_API = f"https://graph.facebook.com/{_GRAPH_API_VERSION}"


class WhatsAppCloudSecrets(BaseModel):
    version: int = 1
    access_token: str
    phone_number_id: str
    waba_id: str = ""
    verify_token: str
    display_phone_number: str = ""
    refresh_token: Optional[str] = None
    expires_at: Optional[str] = None


class MetaEmbeddedSignup(OAuth2AuthCodeBase):
    """Meta's `dialog/oauth` flow. We use it primarily as a way to have
    the admin "Add the bot" with one click; the post_install_hook does
    the heavy lifting (token upgrade, webhook subscribe). Static install
    is the recommended v1 path; this exists so the architecture is
    additive when we want to ship Embedded Signup later."""

    authorize_url = f"https://www.facebook.com/{_GRAPH_API_VERSION}/dialog/oauth"
    token_url = f"{GRAPH_API}/oauth/access_token"
    secrets_model = WhatsAppCloudSecrets
    allows_direct_static_install = True  # Static is preferred in v1.

    def __init__(self):
        super().__init__(
            scopes=[
                "whatsapp_business_management",
                "whatsapp_business_messaging",
                "business_management",
            ],
            client_id_env="META_CLIENT_ID",
            client_secret_env="META_CLIENT_SECRET",
        )

    def parse_token_response(self, body: dict) -> TokenSet:
        if "access_token" not in body:
            raise RuntimeError(f"Meta OAuth failed: {body}")
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=None,
            expires_at=None,  # Meta returns expires_in only for short-lived; long-lived is ~60d.
            extra={"raw_body": body},
        )

    def extract_install_identity(self, body: dict) -> str:
        # Meta auth_code response doesn't carry phone_number_id; the
        # caller (post_install_hook) discovers it via /me/businesses.
        # The extract is best-effort — admin's follow-up form sets the
        # row's external_id authoritatively.
        return ""

    async def post_install_hook(self, channel: BotChannelModel, token_set: TokenSet) -> None:
        """Exchange short-lived for long-lived, then subscribe the
        webhook. Admin provides verify_token in a follow-up form before
        any inbound traffic is accepted (handshake fails until verify_token
        is set)."""
        from models.bot_channels import BotChannels
        from services.secrets import encrypt as fernet_encrypt

        client_id = os.environ.get("META_CLIENT_ID")
        client_secret = os.environ.get("META_CLIENT_SECRET")
        if not client_id or not client_secret:
            log.warning("META_CLIENT_ID/SECRET unset; cannot upgrade Meta token to long-lived")
            return

        async with make_client() as client:
            # Upgrade short → long (60d) lived.
            resp = await client.get(
                f"{GRAPH_API}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "fb_exchange_token": token_set.access_token,
                },
            )
            if resp.status_code == 200:
                long_token = resp.json().get("access_token", token_set.access_token)
            else:
                log.warning("Meta long-lived exchange failed status=%s", resp.status_code)
                long_token = token_set.access_token

            # Discover (waba_id, phone_number_id). If we can't, the
            # admin completes the row via PUT /secrets.
            phone_number_id = ""
            waba_id = ""
            try:
                me = await client.get(
                    f"{GRAPH_API}/me/businesses",
                    headers={"Authorization": f"Bearer {long_token}"},
                )
                me.raise_for_status()
                businesses = me.json().get("data") or []
                if businesses:
                    biz_id = businesses[0]["id"]
                    waba_resp = await client.get(
                        f"{GRAPH_API}/{biz_id}/owned_whatsapp_business_accounts",
                        headers={"Authorization": f"Bearer {long_token}"},
                    )
                    if waba_resp.status_code == 200:
                        wabas = waba_resp.json().get("data") or []
                        if wabas:
                            waba_id = wabas[0]["id"]
                            ph_resp = await client.get(
                                f"{GRAPH_API}/{waba_id}/phone_numbers",
                                headers={"Authorization": f"Bearer {long_token}"},
                            )
                            if ph_resp.status_code == 200:
                                phs = ph_resp.json().get("data") or []
                                if phs:
                                    phone_number_id = phs[0]["id"]
            except Exception:
                log.exception("Meta install discovery failed channel=%s", channel.id)

        # Persist what we have. Admin's follow-up form fills verify_token
        # (and corrects waba_id/phone_number_id if multi-phone account).
        secrets = WhatsAppCloudSecrets(
            access_token=long_token,
            phone_number_id=phone_number_id,
            waba_id=waba_id,
            verify_token="",
        )
        BotChannels.update_credentials(channel.id, fernet_encrypt(secrets.model_dump_json()))
        if phone_number_id:
            BotChannels.update_external_id(channel.id, phone_number_id, display_name=phone_number_id)


class WhatsAppCloudAdapter(ChannelAdapter):
    type = "whatsapp_cloud"
    auth = MetaEmbeddedSignup()
    install_addressing = "by_path"
    default_reply_mode = "async"
    supported_reply_modes = {"async"}
    chat_types_supported = {"dm"}
    capabilities = ChannelCaps(
        supports_attachments=True,
        max_message_length=4000,
    )

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------
    async def handle_handshake(self, request: Request, secrets: Optional[BaseModel]) -> Optional[Any]:
        """Meta's GET ``hub.mode=subscribe`` handshake. Validates the
        admin-chosen ``verify_token`` and echoes the challenge back."""
        if request.method != "GET":
            return None
        params = dict(request.query_params)
        if params.get("hub.mode") != "subscribe":
            return None
        if not isinstance(secrets, WhatsAppCloudSecrets):
            return Response(status_code=403)
        if params.get("hub.verify_token") != secrets.verify_token:
            return Response(status_code=403)
        challenge = params.get("hub.challenge", "")
        return Response(content=challenge, media_type="text/plain")

    async def verify_signature(self, request: Request, secrets: WhatsAppCloudSecrets) -> bool:
        app_secret = os.environ.get("META_APP_SECRET")
        if not app_secret:
            log.error("META_APP_SECRET not set; rejecting all WhatsApp inbound")
            return False
        provided = request.headers.get("x-hub-signature-256", "")
        log.info("WhatsApp inbound signature: %s", provided)
        if not provided.startswith("sha256="):
            log.info("WhatsApp inbound signature missing sha256= prefix: %s", provided)
            return False
        provided_hex = provided[len("sha256="):]
        raw_body = getattr(request, "_body", None) or b""
        digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, provided_hex)

    def extract_event_id(self, request: Request) -> Optional[str]:
        body = _peek_or_read(request)
        if not body:
            return None
        try:
            messages = body["entry"][0]["changes"][0]["value"].get("messages") or []
            if messages:
                return messages[0].get("id")
        except Exception:
            return None
        return None

    async def parse_inbound(self, request: Request, secrets: WhatsAppCloudSecrets) -> Optional[IncomingMessage]:
        body = _peek_or_read(request) or {}
        try:
            value = body["entry"][0]["changes"][0]["value"]
        except Exception:
            return None
        messages = value.get("messages") or []
        if not messages:
            return None
        msg = messages[0]
        msg_type = msg.get("type")
        from_number = msg.get("from", "")
        reply_handle = {
            "to": from_number,
            "phone_number_id": secrets.phone_number_id,
            "context_message_id": msg.get("id"),
        }
        if msg_type != "text":
            # v1: politely tell the user we can only handle text. The
            # canned reply runs through the dispatcher's normal outbound
            # path (retry budget, redaction, single-flight token) — no
            # side effects from this parser.
            return IncomingMessage(
                channel_id="",
                user_external_id=from_number,
                session_external_id=from_number,
                text="",
                chat_type="dm",
                raw_payload=body,
                reply_handle=reply_handle,
                kind="canned_reply",
                canned_reply_text="I can only handle text messages right now.",
            )
        text = (msg.get("text") or {}).get("body", "")
        if not text:
            return None
        return IncomingMessage(
            channel_id="",
            user_external_id=from_number,
            session_external_id=from_number,
            text=text,
            chat_type="dm",
            raw_payload=body,
            reply_handle=reply_handle,
        )

    def is_self(self, secrets: WhatsAppCloudSecrets, raw_payload: dict) -> bool:
        # Inbound never carries our own phone_number_id as `from`.
        return False

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------
    async def send(self, channel: BotChannelModel, reply_handle: Any, out: OutgoingMessage) -> None:
        token = await self.auth.get_access_token(channel)
        phone_id = reply_handle.get("phone_number_id")
        body = {
            "messaging_product": "whatsapp",
            "to": reply_handle.get("to"),
            "type": "text",
            "text": {"body": out.text, "preview_url": False},
        }
        ctx = reply_handle.get("context_message_id")
        if ctx:
            body["context"] = {"message_id": ctx}
        async with make_client() as client:
            resp = await client.post(
                f"{GRAPH_API}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def extract_install_metadata(self, secrets: WhatsAppCloudSecrets) -> InstallMetadata:
        return InstallMetadata(
            external_id=secrets.phone_number_id,
            display_name=secrets.display_phone_number or secrets.phone_number_id,
        )

    async def post_create_install(self, channel: BotChannelModel, secrets: WhatsAppCloudSecrets) -> None:
        """For static installs: subscribe the webhook so Meta starts
        delivering messages. For OAuth installs the post_install_hook
        already did this."""
        if not secrets.access_token or not secrets.phone_number_id:
            raise InstallError("access_token and phone_number_id are required")
        if not secrets.waba_id:
            raise InstallError("waba_id is required — Meta subscribes apps at the WABA level, not the phone number level")
        async with make_client() as client:
            try:
                resp = await client.post(
                    f"{GRAPH_API}/{secrets.waba_id}/subscribed_apps",
                    headers={"Authorization": f"Bearer {secrets.access_token}"},
                )
            except Exception as e:
                raise InstallError(f"unable to reach Meta Graph API: {e}")
            if resp.status_code >= 400:
                err: dict = {}
                try:
                    err = (resp.json() or {}).get("error", {}) or {}
                except Exception:
                    pass
                message = err.get("message") or resp.text[:200] or "no error description"
                tags: List[str] = []
                if err.get("code") is not None:
                    tags.append(f"code={err.get('code')}")
                if err.get("error_subcode") is not None:
                    tags.append(f"subcode={err.get('error_subcode')}")
                if err.get("type"):
                    tags.append(f"type={err.get('type')}")
                if err.get("fbtrace_id"):
                    tags.append(f"fbtrace_id={err.get('fbtrace_id')}")
                tag_str = f" ({', '.join(tags)})" if tags else ""
                raise InstallError(
                    f"Meta subscribe_apps rejected the credentials (status {resp.status_code}) "
                    f"for waba_id={secrets.waba_id}{tag_str}: {message}"
                )

    async def healthcheck(self, channel: BotChannelModel) -> dict:
        try:
            secrets = _decrypt(channel)
        except Exception as e:
            return {"ok": False, "info": {"error": str(e)}}
        try:
            async with make_client() as client:
                resp = await client.get(
                    f"{GRAPH_API}/{secrets.phone_number_id}",
                    headers={"Authorization": f"Bearer {secrets.access_token}"},
                )
                resp.raise_for_status()
                return {"ok": True, "info": resp.json()}
        except Exception as e:
            return {"ok": False, "info": {"error": str(e)}}

    async def revoke(self, channel: BotChannelModel) -> None:
        try:
            secrets = _decrypt(channel)
        except Exception:
            return
        if not secrets.waba_id:
            log.warning("WhatsApp revoke skipped channel=%s: no waba_id", channel.id)
            return
        try:
            async with make_client() as client:
                await client.delete(
                    f"{GRAPH_API}/{secrets.waba_id}/subscribed_apps",
                    headers={"Authorization": f"Bearer {secrets.access_token}"},
                )
        except Exception:
            log.exception("WhatsApp unsubscribe failed channel=%s", channel.id)


def _peek_or_read(request: Request) -> Optional[dict]:
    body = getattr(request, "_body", None)
    if body is None:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def _decrypt(channel: BotChannelModel) -> WhatsAppCloudSecrets:
    from channels.dispatcher import decrypt_secrets
    return decrypt_secrets(channel)  # type: ignore[return-value]


register(WhatsAppCloudAdapter())
