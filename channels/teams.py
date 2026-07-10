"""Microsoft Teams (Bot Framework) channel adapter.

Install model: admin registers a Microsoft App at
``dev.teams.microsoft.com`` and pastes the resulting ``app_id`` /
``app_password`` into our admin form. Teams itself uses manifest
sideload (no OAuth ``/install`` redirect from us); the operator drops
the manifest into a tenant out-of-band.

Inbound: Bot Framework signs every activity with a JWT (issuer
``https://api.botframework.com``, audience = our ``app_id``). We verify
against the Bot Framework JWKS, then sanity-check the ``serviceUrl``
field against an allow-list before reply — preventing a stolen JWT
from redirecting our outbound bearer to a hostile endpoint.

Outbound: AAD client_credentials grant — POST to
``login.microsoftonline.com/botframework.com/oauth2/v2.0/token``,
cache the bearer, then POST the reply to
``{serviceUrl}/v3/conversations/{conv_id}/activities``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Literal, Optional, Tuple

from fastapi import Request
from pydantic import BaseModel, Field

from channels.auth._http import make_client
from channels.auth.base import InstallMetadata
from channels.auth.oauth2_client_credentials import OAuth2ClientCredentials
from channels.base import ChannelAdapter, InstallError
from channels.contracts import ChannelCaps, ChatType, IncomingMessage, OutgoingMessage
from channels.jwt_verifier import extract_unverified_audience, verify_bot_framework_jwt
from channels.registry import register
from models.bot_channels import BotChannelModel

log = logging.getLogger(__name__)

# Bot Framework public-cloud service-url prefixes. Government cloud
# variants (``smba.gov.trafficmanager.net`` etc.) can be added per
# install via the ``valid_service_url_prefixes`` field.
DEFAULT_SERVICE_URL_PREFIXES = [
    "https://smba.trafficmanager.net/",
]

AAD_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
BOT_FRAMEWORK_SCOPE = "https://api.botframework.com/.default"

_MENTION_RE = re.compile(r"<at[^>]*>.*?</at>", flags=re.IGNORECASE | re.DOTALL)


class TeamsSecrets(BaseModel):
    version: int = 1
    app_id: str
    app_password: str
    tenant_id: Optional[str] = None  # Single-tenant only
    app_type: Literal["MultiTenant", "SingleTenant"] = "MultiTenant"
    valid_service_url_prefixes: List[str] = Field(default_factory=lambda: list(DEFAULT_SERVICE_URL_PREFIXES))


class _TeamsAuth(OAuth2ClientCredentials):
    secrets_model = TeamsSecrets

    def token_url_for(self, secrets: BaseModel) -> str:
        # Multi-tenant Bot Framework apps mint at the special
        # ``botframework.com`` tenant; single-tenant apps use their
        # AAD tenant id.
        assert isinstance(secrets, TeamsSecrets)
        tenant = secrets.tenant_id if secrets.app_type == "SingleTenant" and secrets.tenant_id else "botframework.com"
        return AAD_TOKEN_URL_TEMPLATE.format(tenant=tenant)

    def scope_for(self, secrets: BaseModel) -> str:
        return BOT_FRAMEWORK_SCOPE

    def client_credentials_for(self, secrets: BaseModel) -> Tuple[str, str]:
        assert isinstance(secrets, TeamsSecrets)
        return secrets.app_id, secrets.app_password


class TeamsAdapter(ChannelAdapter):
    type = "teams"
    auth = _TeamsAuth()
    install_addressing = "by_payload"
    default_reply_mode = "async"
    supported_reply_modes = {"async"}
    chat_types_supported = {"dm", "channel"}
    capabilities = ChannelCaps(
        supports_threading=True,
        supports_attachments=True,
        max_message_length=8000,
    )

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------
    def extract_install_key(self, request: Request) -> Optional[str]:
        """Multi-tenant Teams apps share one ``app_id`` across N tenants.
        Each registered Teams app in our deployment is its own
        ``BotChannel`` row, keyed by the app_id (which we pull from the
        JWT audience — the body's ``recipient.id`` would also work but
        the JWT is the authoritative carrier).
        """
        bearer = request.headers.get("authorization", "")
        if not bearer.lower().startswith("bearer "):
            return None
        token = bearer[7:].strip()
        return extract_unverified_audience(token)

    async def verify_signature(self, request: Request, secrets: TeamsSecrets) -> bool:
        bearer = request.headers.get("authorization", "")
        if not bearer.lower().startswith("bearer "):
            return False
        token = bearer[7:].strip()
        try:
            await verify_bot_framework_jwt(token, audience=secrets.app_id)
        except PermissionError as e:
            log.warning("Teams JWT verify failed: %s", e)
            return False

        # serviceUrl allow-list defense — reject any inbound that
        # advertises a reply target we don't trust. A valid JWT alone
        # is not enough to direct our outbound bearer.
        body = _peek(request)
        if not body:
            return False
        service_url = body.get("serviceUrl") or ""
        if not _is_trusted_service_url(service_url, secrets.valid_service_url_prefixes):
            log.warning("Teams inbound has untrusted serviceUrl=%s", service_url)
            return False
        return True

    def extract_event_id(self, request: Request) -> Optional[str]:
        body = _peek(request)
        if not body:
            return None
        return body.get("id")

    async def parse_inbound(self, request: Request, secrets: TeamsSecrets) -> Optional[IncomingMessage]:
        body = _peek(request) or {}
        if body.get("type") != "message":
            return None
        sender = body.get("from") or {}
        if sender.get("id") == f"28:{secrets.app_id}":
            return None
        text = (body.get("text") or "").strip()
        if not text:
            return None
        text = _strip_mentions(text)

        conversation = body.get("conversation") or {}
        conv_type = conversation.get("conversationType") or ""
        if conv_type == "personal":
            chat_type: ChatType = "dm"
        elif conv_type in ("channel", "groupChat"):
            chat_type = "channel"
            # In channels, Bot Framework already filters to bot mentions,
            # but defense-in-depth: only proceed when our app_id is in
            # the entities list.
            if not _mentions_us(body, secrets.app_id):
                return None
        else:
            return None

        service_url = body.get("serviceUrl") or ""
        return IncomingMessage(
            channel_id="",
            user_external_id=sender.get("aadObjectId") or sender.get("id") or "",
            session_external_id=conversation.get("id") or "",
            text=text,
            chat_type=chat_type,
            raw_payload=body,
            reply_handle={
                "service_url": service_url,
                "conversation_id": conversation.get("id"),
                "activity_id": body.get("id"),
            },
        )

    def is_self(self, secrets: TeamsSecrets, raw_payload: dict) -> bool:
        sender = raw_payload.get("from") or {}
        return sender.get("id") == f"28:{secrets.app_id}"

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------
    async def send(self, channel: BotChannelModel, reply_handle: Any, out: OutgoingMessage) -> None:
        if not isinstance(reply_handle, dict):
            return
        secrets = _decrypt(channel)
        service_url = reply_handle.get("service_url") or ""
        if not _is_trusted_service_url(service_url, secrets.valid_service_url_prefixes):
            log.error("Refusing send: untrusted serviceUrl=%s channel=%s", service_url, channel.id)
            return
        token = await self.auth.get_access_token(channel)
        conv_id = reply_handle.get("conversation_id")
        activity_id = reply_handle.get("activity_id")
        body = {
            "type": "message",
            "text": out.text,
            "textFormat": "markdown",
        }
        if activity_id:
            body["replyToId"] = activity_id
        url = f"{service_url.rstrip('/')}/v3/conversations/{conv_id}/activities"
        async with make_client() as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def extract_install_metadata(self, secrets: TeamsSecrets) -> InstallMetadata:
        return InstallMetadata(
            external_id=secrets.app_id,
            display_name=f"Teams app {secrets.app_id[:8]}…",
        )

    async def post_create_install(self, channel: BotChannelModel, secrets: TeamsSecrets) -> None:
        # Validate the app credentials by minting an AAD token. If this
        # fails the row is unusable, so reject the install up front
        # rather than letting the admin discover it via a silent inbound
        # 401 storm later.
        try:
            await self.auth.get_access_token(channel)
        except Exception as e:
            raise InstallError(
                f"Bot Framework AAD token mint failed — check app_id and app_password: {e}"
            )

    async def healthcheck(self, channel: BotChannelModel) -> dict:
        try:
            secrets = _decrypt(channel)
            await self.auth.get_access_token(channel)
            return {
                "ok": True,
                "info": {"app_id": secrets.app_id, "app_type": secrets.app_type, "tenant_id": secrets.tenant_id},
            }
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


def _strip_mentions(text: str) -> str:
    return _MENTION_RE.sub("", text).strip()


def _mentions_us(body: dict, app_id: str) -> bool:
    entities = body.get("entities") or []
    target = f"28:{app_id}"
    for ent in entities:
        if ent.get("type") != "mention":
            continue
        mentioned = (ent.get("mentioned") or {}).get("id", "")
        if mentioned == target:
            return True
    return False


def _is_trusted_service_url(url: str, prefixes: List[str]) -> bool:
    return any(url.startswith(p) for p in prefixes if p)


def _decrypt(channel: BotChannelModel) -> TeamsSecrets:
    from channels.dispatcher import decrypt_secrets
    return decrypt_secrets(channel)  # type: ignore[return-value]


register(TeamsAdapter())
