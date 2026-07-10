"""Slack channel adapter — per-bot static install.

Each ``BotChannel`` row carries the full Slack-app config (signing
secret, bot token) for that bot. The deployment hosts N independent
Slack apps — one per bot, one per workspace install — with no shared
deployment-wide credentials. Operator workflow:

1. Create a Slack app at https://api.slack.com/apps (per bot).
2. Add bot scopes; install to a workspace; copy the bot token
   (``xoxb-…``) and the signing secret from "Basic information".
3. Paste both into the channels admin UI for the bot.
4. The channel row's webhook URL is the value to paste into Slack's
   "Event Subscriptions" config; subscribe to ``message.im`` and
   ``app_mention``.

We validate the install up front by calling ``auth.test`` with the bot
token to confirm it works and to populate ``team_id`` /
``bot_user_id`` / ``team_name``. A future OAuth ("Add to Slack")
release stays additive — it'd populate the same secrets via redirect
instead of paste.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional

import mistune

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

SLACK_API = "https://slack.com/api"
SIGNATURE_REPLAY_WINDOW = 60 * 5  # 5 minutes


class SlackSecrets(BaseModel):
    version: int = 1
    # Pasted by the operator (per-bot Slack app properties):
    access_token: str
    signing_secret: str
    # Filled in by ``auth.test`` during install (per-workspace):
    team_id: str = ""
    bot_user_id: str = ""
    team_name: str = ""
    granted_scopes: List[str] = Field(default_factory=list)


class _SlackAuth(StaticToken):
    """Static-token strategy specialized to read Slack's `access_token`."""

    def __init__(self):
        super().__init__(secrets_model=SlackSecrets, token_field="access_token")


class SlackAdapter(ChannelAdapter):
    type = "slack"
    auth = _SlackAuth()
    install_addressing = "by_payload"
    default_reply_mode = "async"
    supported_reply_modes = {"async"}
    chat_types_supported = {"dm", "channel", "thread"}
    capabilities = ChannelCaps(
        supports_streaming=False,
        supports_threading=True,
        supports_edit_after_post=True,
        supports_attachments=False,
        supports_rich_blocks=False,  # plain text in v1
        max_message_length=3500,
    )

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------
    async def handle_handshake(self, request: Request, secrets: Optional[BaseModel]) -> Optional[Any]:
        body = _peek_or_read(request)
        if not body:
            return None
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge", "")}
        return None

    async def verify_signature(self, request: Request, secrets: SlackSecrets) -> bool:
        if not secrets.signing_secret:
            log.error("Slack channel has empty signing_secret; rejecting inbound")
            return False
        timestamp = request.headers.get("x-slack-request-timestamp", "")
        provided_sig = request.headers.get("x-slack-signature", "")
        if not timestamp or not provided_sig:
            return False
        try:
            ts_int = int(timestamp)
        except ValueError:
            return False
        if abs(time.time() - ts_int) > SIGNATURE_REPLAY_WINDOW:
            return False
        raw_body = getattr(request, "_body", None) or b""
        basestring = b"v0:" + timestamp.encode("ascii") + b":" + raw_body
        digest = hmac.new(secrets.signing_secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
        expected = f"v0={digest}"
        return hmac.compare_digest(expected, provided_sig)

    def extract_install_key(self, request: Request) -> Optional[str]:
        body = _peek_or_read(request)
        if not body:
            return None
        return body.get("team_id") or (body.get("team") or {}).get("id")

    def extract_event_id(self, request: Request) -> Optional[str]:
        body = _peek_or_read(request)
        if not body:
            return None
        return body.get("event_id")

    async def parse_inbound(self, request: Request, secrets: SlackSecrets) -> Optional[IncomingMessage]:
        body = _peek_or_read(request) or {}
        if body.get("type") != "event_callback":
            return None
        event = body.get("event") or {}
        ev_type = event.get("type")
        if ev_type not in ("message", "app_mention"):
            return None
        subtype = event.get("subtype")
        if subtype not in (None, "file_share"):
            return None
        if event.get("bot_id") or event.get("user") == secrets.bot_user_id:
            return None
        text = event.get("text") or ""
        if not text:
            return None

        channel_kind = event.get("channel_type")  # "im" / "channel" / "group" / "mpim"
        thread_ts = event.get("thread_ts")
        ts = event.get("ts")
        chat_type: ChatType
        if channel_kind == "im":
            chat_type = "dm"
        elif thread_ts and thread_ts != ts:
            chat_type = "thread"
        else:
            chat_type = "channel"

        if ev_type == "app_mention" or chat_type == "channel":
            text = _strip_mention(text, secrets.bot_user_id)
        if not text:
            return None

        channel_external = event.get("channel")
        session_external_id = f"{channel_external}:{thread_ts or ts}"

        return IncomingMessage(
            channel_id="",
            user_external_id=event.get("user", ""),
            session_external_id=session_external_id,
            text=text,
            chat_type=chat_type,
            raw_payload=body,
            reply_handle={
                "channel": channel_external,
                "thread_ts": thread_ts or ts,
            },
        )

    def is_self(self, secrets: SlackSecrets, raw_payload: dict) -> bool:
        event = raw_payload.get("event") or {}
        return bool(event.get("bot_id")) or event.get("user") == secrets.bot_user_id

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------
    async def send(self, channel: BotChannelModel, reply_handle: Any, out: OutgoingMessage) -> None:
        token = await self.auth.get_access_token(channel)
        async with make_client() as client:
            resp = await client.post(
                f"{SLACK_API}/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "channel": reply_handle.get("channel"),
                    "thread_ts": reply_handle.get("thread_ts"),
                    "text": _md_to_mrkdwn(out.text),
                },
            )
            resp.raise_for_status()
            body = resp.json()
            if not body.get("ok"):
                raise RuntimeError(f"Slack chat.postMessage failed: {body.get('error')}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def extract_install_metadata(self, secrets: SlackSecrets) -> InstallMetadata:
        if not secrets.access_token.startswith("xoxb-"):
            raise InstallError(
                "Slack bot tokens start with 'xoxb-'. Make sure you copied the "
                "Bot User OAuth Token, not the User OAuth Token."
            )
        if not secrets.signing_secret:
            raise InstallError("signing_secret is required (find it on the app's Basic Information page)")
        async with make_client() as client:
            resp = await client.post(
                f"{SLACK_API}/auth.test",
                headers={"Authorization": f"Bearer {secrets.access_token}"},
            )
        try:
            body = resp.json()
        except Exception:
            body = {}
        if not body.get("ok"):
            raise InstallError(
                f"Slack auth.test rejected the bot token: {body.get('error') or resp.text[:200]}"
            )
        team_id = body.get("team_id") or ""
        team_name = body.get("team") or ""
        bot_user_id = body.get("user_id") or ""
        if not team_id or not bot_user_id:
            raise InstallError(f"Slack auth.test response missing team_id/user_id: {body}")
        # Stamp discovered fields onto the secrets so persisted blob is
        # complete. The dispatcher's verify_signature/parse_inbound rely
        # on `bot_user_id` being populated.
        secrets.team_id = team_id
        secrets.bot_user_id = bot_user_id
        secrets.team_name = team_name
        return InstallMetadata(
            external_id=team_id,
            display_name=team_name or team_id,
        )

    async def healthcheck(self, channel: BotChannelModel) -> dict:
        try:
            token = await self.auth.get_access_token(channel)
            async with make_client() as client:
                resp = await client.post(
                    f"{SLACK_API}/auth.test",
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                body = resp.json()
            return {"ok": bool(body.get("ok")), "info": body}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "info": {"error": str(e)}}

    async def revoke(self, channel: BotChannelModel) -> None:
        try:
            token = await self.auth.get_access_token(channel)
        except Exception:
            return
        try:
            async with make_client() as client:
                await client.post(
                    f"{SLACK_API}/auth.revoke",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except Exception:
            log.exception("Slack auth.revoke failed channel=%s", channel.id)


# ---------------------------------------------------------------------------
# Markdown → mrkdwn
# ---------------------------------------------------------------------------
# LLMs emit standard CommonMark — ``**bold**``, ``*italic*``,
# ``[label](url)``, ``# heading``. Slack's ``mrkdwn`` flavor uses a
# different syntax (``*bold*``, ``_italic_``, ``<url|label>``, no
# headings). Posting raw Markdown leaves literal asterisks and bracket
# pairs in the channel, so we translate before sending.
#
# We parse with ``mistune`` (an existing dep) and walk the AST. Going
# through a real parser instead of regex lets nested formatting, code
# fences, lists, and block quotes all compose correctly without the
# regex order-of-operations gotchas that bite hand-rolled translators.
_MD_PARSER = mistune.create_markdown(renderer=None, plugins=["strikethrough"])


class _SlackMrkdwnRenderer:
    """Walks mistune's AST and emits Slack mrkdwn.

    Each block-level handler ends its output with ``\\n\\n`` (one blank
    line) so adjacent blocks always render with proper separation —
    mistune doesn't always emit ``blank_line`` tokens between, e.g., a
    list and a following paragraph. ``render`` collapses any runs of 3+
    newlines back down to a single blank line at the end.
    """

    def render(self, tokens: List[Dict[str, Any]]) -> str:
        out = "".join(self._block(t) for t in tokens)
        # Collapse runs of 3+ newlines down to a single blank line
        # without pulling in ``re`` for one call site.
        while "\n\n\n" in out:
            out = out.replace("\n\n\n", "\n\n")
        return out.rstrip("\n")

    # ---- block dispatch ------------------------------------------------
    def _block(self, t: Dict[str, Any]) -> str:
        handler = getattr(self, f"_b_{t['type']}", None)
        if handler is not None:
            return handler(t)
        # Unknown block — fall back to inline children to avoid dropping
        # content silently.
        return self._inline_children(t) + "\n\n"

    def _b_paragraph(self, t: Dict[str, Any]) -> str:
        return self._inline_children(t) + "\n\n"

    def _b_heading(self, t: Dict[str, Any]) -> str:
        # Slack mrkdwn has no ``#`` headings; bold is the closest match.
        return f"*{self._inline_children(t)}*\n\n"

    def _b_blank_line(self, _t: Dict[str, Any]) -> str:
        # Block emitters already add their own trailing blank line; the
        # final ``\n{3,}`` collapse handles any double-up.
        return "\n"

    def _b_thematic_break(self, _t: Dict[str, Any]) -> str:
        return "---\n\n"

    def _b_block_code(self, t: Dict[str, Any]) -> str:
        body = t.get("raw", "")
        if not body.endswith("\n"):
            body += "\n"
        return f"```\n{body}```\n\n"

    def _b_block_quote(self, t: Dict[str, Any]) -> str:
        inner = "".join(self._block(c) for c in t.get("children", [])).rstrip("\n")
        if not inner:
            return ""
        return "\n".join(f"> {line}" for line in inner.splitlines()) + "\n\n"

    def _b_list(self, t: Dict[str, Any]) -> str:
        attrs = t.get("attrs") or {}
        ordered = bool(attrs.get("ordered"))
        indent = "  " * int(attrs.get("depth", 0))
        lines: List[str] = []
        for i, item in enumerate(t.get("children", []), start=1):
            marker = f"{i}." if ordered else "•"
            body = "".join(self._block(c) for c in item.get("children", [])).rstrip("\n")
            item_lines = body.splitlines() or [""]
            item_lines[0] = f"{indent}{marker} {item_lines[0]}"
            lines.append("\n".join(item_lines))
        return "\n".join(lines) + "\n\n"

    def _b_block_text(self, t: Dict[str, Any]) -> str:
        # ``block_text`` is the inner wrapper inside list items — keep
        # tight, the surrounding list spacing handles separation.
        return self._inline_children(t) + "\n"

    def _b_block_html(self, t: Dict[str, Any]) -> str:
        # Raw HTML — Slack won't render it, so pass through as text.
        return t.get("raw", "")

    # ---- inline dispatch -----------------------------------------------
    def _inline_children(self, t: Dict[str, Any]) -> str:
        return "".join(self._inline(c) for c in t.get("children", []))

    def _inline(self, t: Dict[str, Any]) -> str:
        handler = getattr(self, f"_i_{t['type']}", None)
        if handler is not None:
            return handler(t)
        return t.get("raw", "")

    def _i_text(self, t: Dict[str, Any]) -> str:
        return t.get("raw", "")

    def _i_strong(self, t: Dict[str, Any]) -> str:
        return f"*{self._inline_children(t)}*"

    def _i_emphasis(self, t: Dict[str, Any]) -> str:
        return f"_{self._inline_children(t)}_"

    def _i_codespan(self, t: Dict[str, Any]) -> str:
        return f"`{t.get('raw', '')}`"

    def _i_strikethrough(self, t: Dict[str, Any]) -> str:
        return f"~{self._inline_children(t)}~"

    def _i_link(self, t: Dict[str, Any]) -> str:
        url = (t.get("attrs") or {}).get("url", "")
        label = self._inline_children(t)
        if not url:
            return label
        if label == url or not label:
            return f"<{url}>"
        return f"<{url}|{label}>"

    def _i_image(self, t: Dict[str, Any]) -> str:
        url = (t.get("attrs") or {}).get("url", "")
        alt = self._inline_children(t) or "image"
        return f"<{url}|{alt}>" if url else alt

    def _i_linebreak(self, _t: Dict[str, Any]) -> str:
        return "\n"

    def _i_softbreak(self, _t: Dict[str, Any]) -> str:
        return "\n"

    def _i_inline_html(self, t: Dict[str, Any]) -> str:
        return t.get("raw", "")


_MRKDWN_RENDERER = _SlackMrkdwnRenderer()


def _md_to_mrkdwn(text: str) -> str:
    if not text:
        return text
    try:
        tokens = _MD_PARSER(text)
    except Exception:
        log.exception("mistune parse failed; falling back to raw text")
        return text
    return _MRKDWN_RENDERER.render(tokens)


def _strip_mention(text: str, bot_user_id: str) -> str:
    if not bot_user_id:
        return text.strip()
    needle = f"<@{bot_user_id}>"
    idx = text.find(needle)
    if idx < 0:
        return text.strip()
    return (text[:idx] + text[idx + len(needle):]).strip()


def _peek_or_read(request: Request) -> Optional[dict]:
    body = getattr(request, "_body", None)
    if body is None:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


register(SlackAdapter())
