"""Generic webhook dispatcher.

One implementation handles all platforms. Adapter contributes:
- Signature verification
- Inbound parsing
- Self-message filter
- Outbound sender (or sync formatter)

Dispatcher owns:
- Channel resolution (by_path / by_payload)
- Two-phase idempotency
- Identity hashing
- Sync vs async branching
- Outbound chunking, citation footnotes, retries
- Error fallback so the user is never ghosted
- Soft cap on inflight async tasks
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from channels.base import ChannelAdapter, ReplyMode
from channels.contracts import IncomingMessage, OutgoingMessage
from channels.registry import get as registry_get
from models.bot_channels import BotChannelModel, BotChannels
from models.webhook_event_dedupe import WebhookEventDedupes
from services.secrets import decrypt as fernet_decrypt

log = logging.getLogger(__name__)

DEFAULT_ERROR_REPLY = "Sorry, something went wrong on my end. Please try again."

# Soft cap on async background work. Single-replica until Redis/queue
# arrives — exceeding this means we're ingesting faster than the agent
# can drain. Returning 429 makes Slack/Teams/WhatsApp retry, which our
# two-phase dedupe then handles correctly.
_MAX_INFLIGHT = 100
_inflight = 0
_inflight_lock = asyncio.Lock()


def decrypt_secrets(channel: BotChannelModel) -> BaseModel:
    """Fernet-decrypt a channel's credentials and validate against the
    adapter's ``secrets_model``. Single read path used by every adapter
    and every auth strategy.
    """
    adapter = registry_get(channel.type)
    if adapter is None:
        raise RuntimeError(f"No adapter registered for type={channel.type}")
    if not channel.credentials_encrypted:
        raise RuntimeError(f"Channel {channel.id} has no credentials stored")
    plaintext = fernet_decrypt(channel.credentials_encrypted)
    if not plaintext:
        raise RuntimeError(f"Channel {channel.id} credentials decrypt empty")
    payload = json.loads(plaintext)
    return adapter.auth.secrets_model.model_validate(payload)


def emly_id_for(bot_id: str, channel_external_id: str, ext_id: str) -> str:
    """Deterministic, stable-across-reinstall identity hash.

    Uses the install's stable platform id (Slack ``team_id``, WhatsApp
    ``phone_number_id``, Telegram bot id) — not the channel row's UUID
    — so a reinstall that produces the same external_id keeps the same
    emly user/session mapping for returning users.
    """
    raw = f"{bot_id}:{channel_external_id}:{ext_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _resolve_reply_mode(channel: BotChannelModel, adapter: ChannelAdapter) -> ReplyMode:
    cfg = channel.config_json or {}
    requested = cfg.get("reply_mode")
    if requested in adapter.supported_reply_modes:
        return requested  # type: ignore[return-value]
    return adapter.default_reply_mode


# ---------------------------------------------------------------------------
# Outbound helpers
# ---------------------------------------------------------------------------
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _chunk_text(text: str, max_len: Optional[int]) -> List[str]:
    if not max_len or len(text) <= max_len:
        return [text]
    chunks: List[str] = []
    remaining = text
    while len(remaining) > max_len:
        # Prefer paragraph break, then sentence, then hard cut.
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut < max_len // 2:
            cut = remaining.rfind(". ", 0, max_len)
            if cut < max_len // 2:
                cut = max_len
            else:
                cut += 1  # keep the period
        else:
            cut += 2
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _coerce_dict(value: Any) -> Dict[str, Any]:
    """RAG payloads sometimes carry ``og``/``payload`` as the JSON string
    they were ingested as. Parse defensively so the citation renderer can
    treat both shapes uniformly."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        for parser in (json.loads, _safe_literal_eval):
            try:
                parsed = parser(value)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _safe_literal_eval(value: str) -> Any:
    import ast

    return ast.literal_eval(value)


def _citation_label(c: dict) -> Tuple[str, Optional[str]]:
    """Pick a human-readable ``(title, url)`` for a citation.

    Citations come back from RAG search shaped as
    ``{"metadata": {...}, "chunk": "..."}``. The original footnote
    renderer read top-level keys (``title``, ``url``, ``source``) that
    only exist after legacy post-processing — for the modern shape it
    always fell through to a bare ``"source"``. Look in ``metadata``
    first, fall back to top-level keys for forward/back compatibility.
    """
    metadata = c.get("metadata") or {}
    og = _coerce_dict(c.get("og") or metadata.get("og"))
    payload = _coerce_dict(c.get("payload") or metadata.get("payload"))

    source = metadata.get("source") or c.get("source") or ""
    title = (
        og.get("title")
        or payload.get("title")
        or metadata.get("title")
        or c.get("title")
        or metadata.get("filename")
        or c.get("file_name")
        or (os.path.basename(source) if source else None)
        or "source"
    )
    url = (
        metadata.get("source_url")
        or og.get("url")
        or payload.get("url")
        or c.get("url")
    )
    if not url and source.startswith(("http://", "https://")):
        url = source
    return title, url


def _append_citation_footnotes(text: str, citations: List[dict]) -> str:
    if not citations:
        return text
    lines = ["", "Sources:"]
    for i, c in enumerate(citations, start=1):
        title, url = _citation_label(c)
        if url:
            lines.append(f"[{i}] {title} — {url}")
        else:
            lines.append(f"[{i}] {title}")
    return text + "\n" + "\n".join(lines)


def _prepare_outgoing(adapter: ChannelAdapter, out: OutgoingMessage) -> List[OutgoingMessage]:
    text = out.text
    if not adapter.capabilities.supports_rich_blocks:
        text = _append_citation_footnotes(text, out.citations or [])
    chunks = _chunk_text(text, adapter.capabilities.max_message_length)
    return [
        OutgoingMessage(text=chunk, citations=out.citations if i == 0 else [], format_hints=out.format_hints)
        for i, chunk in enumerate(chunks)
    ]


async def send_with_retry(
    adapter: ChannelAdapter,
    channel: BotChannelModel,
    reply_handle: Any,
    out: OutgoingMessage,
    max_attempts: int = 3,
) -> None:
    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            await adapter.send(channel, reply_handle, out)
            return
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status not in _RETRY_STATUSES or attempt == max_attempts:
                log.exception(
                    "Outbound %s failed (status=%s, attempt=%d/%d)",
                    adapter.type, status, attempt, max_attempts,
                )
                raise
            # Honor platform Retry-After when present (Slack/Meta send
            # it as a header; Telegram puts it in the JSON body, which
            # ``adapter.parse_retry_after`` knows how to extract). Fall
            # back to exponential delay otherwise.
            try:
                hint = adapter.parse_retry_after(e.response)
            except Exception:
                hint = None
            sleep_for = float(hint) if hint and hint > 0 else delay
            log.warning(
                "Outbound %s status=%s attempt=%d/%d, retrying in %.1fs",
                adapter.type, status, attempt, max_attempts, sleep_for,
            )
            await asyncio.sleep(sleep_for)
            delay *= 2
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if attempt == max_attempts:
                log.exception("Outbound %s transport error after %d attempts", adapter.type, attempt)
                raise
            log.warning(
                "Outbound %s transport %s attempt=%d/%d, retrying in %.1fs",
                adapter.type, e.__class__.__name__, attempt, max_attempts, delay,
            )
            await asyncio.sleep(delay)
            delay *= 2


# ---------------------------------------------------------------------------
# Logging discipline
# ---------------------------------------------------------------------------
# Two-pattern strategy:
#   1. ``_NAMED_VALUE_RE`` matches ``<name><sep><value>`` shapes — captures
#      the name in group 1, the value in group 2, and substitutes
#      ``\1<redacted>`` so the field name stays in the log (useful) but
#      the value never does.
#   2. ``_BARE_TOKEN_RE`` matches token shapes without a leading name —
#      anything that looks like a Slack ``xoxb-…``, a Meta long-lived
#      token, a JWT, or an AWS-shaped key. Full match → ``<redacted>``.
#
# Order matters: bare-token first (so `bearer eyJ…` becomes
# `bearer <redacted>` via #2 if #1 hadn't already caught the named
# header).
_NAMED_VALUE_RE = re.compile(
    r"(?P<name>"
    r"authorization\s*[:=]?\s*"
    r"|bearer\s+"
    r"|x-[\w-]*signature[\w-]*\s*[:=]?\s*"
    r"|x-telegram-bot-api-secret-token\s*[:=]?\s*"
    r"|\b[\w-]*(?:token|secret|key|password|passphrase|api[_-]?key)\b\s*[:=]\s*"
    r")"
    r"(?P<value>\S+)",
    re.IGNORECASE,
)

_BARE_TOKEN_RE = re.compile(
    r"\b(?:"
    r"xox[abprt]-[A-Za-z0-9-]+"            # Slack bot/user/refresh tokens
    r"|EAA[A-Za-z0-9_-]{20,}"               # Meta user / system tokens
    r"|eyJ[\w-]+\.[\w-]+\.[\w-]+"           # JWTs (Bot Framework / Google Chat)
    r"|AKIA[0-9A-Z]{16}"                    # AWS access key id (defense in depth)
    r"|ghp_[A-Za-z0-9]{30,}"                # GitHub personal access tokens
    r"|sk-[A-Za-z0-9]{20,}"                 # OpenAI-shaped keys
    r")"
)


class _RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        try:
            msg = record.getMessage()
        except Exception:
            return True
        rewritten = _NAMED_VALUE_RE.sub(lambda m: f"{m.group('name')}<redacted>", msg)
        rewritten = _BARE_TOKEN_RE.sub("<redacted>", rewritten)
        if rewritten != msg:
            record.msg = rewritten
            record.args = ()
        return True


def install_redaction_filter() -> None:
    f = _RedactionFilter()
    for name in ("channels", "channels.dispatcher", "channels.auth"):
        logging.getLogger(name).addFilter(f)


# ---------------------------------------------------------------------------
# Dispatcher entry point
# ---------------------------------------------------------------------------
async def _resolve_channel(
    adapter: ChannelAdapter,
    request: Request,
    channel_id: Optional[str],
    raw_body: bytes,
) -> BotChannelModel:
    if adapter.install_addressing == "by_path":
        if not channel_id:
            raise HTTPException(status_code=404, detail="channel_id required for path-routed adapters")
        ch = BotChannels.get_by_id(channel_id)
        if ch is None or not ch.is_active or ch.type != adapter.type:
            raise HTTPException(status_code=404, detail="channel not found")
        return ch
    # by_payload
    install_key = adapter.extract_install_key(request)
    if not install_key:
        raise HTTPException(status_code=400, detail="install key not present in payload")
    ch = BotChannels.get_by_external(adapter.type, install_key)
    if ch is None or not ch.is_active:
        raise HTTPException(status_code=404, detail=f"no install registered for {adapter.type} key={install_key}")
    return ch


async def handle_handshake(
    channel_type: str,
    channel_id: Optional[str],
    request: Request,
) -> Response:
    """GET-handshake entry point. Used by WhatsApp's hub.challenge."""
    adapter = registry_get(channel_type)
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"unknown channel type {channel_type}")
    secrets: Optional[BaseModel] = None
    if channel_id:
        ch = BotChannels.get_by_id(channel_id)
        if ch and ch.is_active and ch.credentials_encrypted:
            secrets = decrypt_secrets(ch)
    result = await adapter.handle_handshake(request, secrets)
    if result is None:
        raise HTTPException(status_code=404, detail="no handshake")
    if isinstance(result, Response):
        return result
    if isinstance(result, str):
        return Response(content=result, media_type="text/plain")
    return JSONResponse(content=result)


async def handle_inbound(
    channel_type: str,
    channel_id: Optional[str],
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    global _inflight
    adapter = registry_get(channel_type)
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"unknown channel type {channel_type}")

    raw_body = await request.body()

    # url_verification / hub.challenge can arrive on the POST endpoint
    # too — let the adapter intercept before anything else.
    pre = await adapter.handle_handshake(request, None)
    if pre is not None:
        if isinstance(pre, Response):
            return pre
        return JSONResponse(content=pre)

    channel = await _resolve_channel(adapter, request, channel_id, raw_body)
    secrets = decrypt_secrets(channel)

    if not await adapter.verify_signature(request, secrets):
        log.warning("Signature verification failed type=%s channel=%s", channel.type, channel.id)
        raise HTTPException(status_code=401, detail="signature verification failed")

    event_id = adapter.extract_event_id(request)
    dedupe_key: Optional[str] = None
    if event_id:
        claimed, status = WebhookEventDedupes.claim(channel.type, event_id)
        if not claimed:
            log.info("Dropping duplicate event type=%s id=%s status=%s", channel.type, event_id, status)
            return JSONResponse(content=adapter.async_ack_body)
        dedupe_key = event_id

    incoming = await adapter.parse_inbound(request, secrets)
    if incoming is None:
        if dedupe_key:
            WebhookEventDedupes.mark_done(channel.type, dedupe_key)
        return JSONResponse(content=adapter.async_ack_body)

    if adapter.is_self(secrets, incoming.raw_payload):
        if dedupe_key:
            WebhookEventDedupes.mark_done(channel.type, dedupe_key)
        return JSONResponse(content=adapter.async_ack_body)

    incoming.channel_id = channel.id

    # Adapter declared this is a canned reply — skip the agent entirely
    # and ship the fixed text through the same retry/redaction pipeline
    # as a normal reply.
    if incoming.kind == "canned_reply":
        background_tasks.add_task(
            _safe_send,
            channel.type,
            channel.id,
            incoming.reply_handle,
            OutgoingMessage(text=incoming.canned_reply_text or DEFAULT_ERROR_REPLY),
        )
        if dedupe_key:
            WebhookEventDedupes.mark_done(channel.type, dedupe_key)
        return JSONResponse(content=adapter.async_ack_body)

    bot_id = channel.bot_id
    if not channel.external_id:
        # An incomplete install — e.g. WhatsApp OAuth callback before the
        # admin has filled in `verify_token` / discovered `phone_number_id`,
        # or any future static install where `extract_install_metadata`
        # didn't populate the field. Hashing user identity under a
        # placeholder would orphan history once the real id arrives, so
        # refuse rather than degrade.
        log.error(
            "Refusing dispatch for type=%s channel=%s: external_id not set "
            "(install incomplete; admin must finish provisioning)",
            channel.type, channel.id,
        )
        if dedupe_key:
            WebhookEventDedupes.mark_failed(channel.type, dedupe_key)
        raise HTTPException(status_code=503, detail="channel install incomplete")
    install_key = channel.external_id
    user_id = emly_id_for(bot_id, install_key, incoming.user_external_id)
    session_id = emly_id_for(bot_id, install_key, incoming.session_external_id)

    reply_mode = _resolve_reply_mode(channel, adapter)

    if reply_mode == "sync":
        sync_timeout = adapter.sync_response_timeout
        agent_task: Optional[asyncio.Task] = None
        try:
            if sync_timeout is not None:
                # Hybrid path: race the agent against the sync window so
                # we can ack the webhook fast (avoiding "App isn't
                # responding" on Google Chat) when the agent overruns,
                # while keeping the inline reply for the common case.
                agent_task = asyncio.create_task(
                    _run_agent(bot_id, user_id, session_id, incoming.text, channel_id=channel.id)
                )
                out = await asyncio.wait_for(asyncio.shield(agent_task), timeout=sync_timeout)
            else:
                out = await _run_agent(bot_id, user_id, session_id, incoming.text, channel_id=channel.id)
        except asyncio.TimeoutError:
            log.info(
                "Sync reply window (%.1fs) elapsed type=%s channel=%s; falling back to async ack",
                sync_timeout, channel.type, channel.id,
            )
            assert agent_task is not None
            async with _inflight_lock:
                if _inflight >= _MAX_INFLIGHT:
                    agent_task.cancel()
                    log.warning("Async inflight cap reached after sync timeout; shedding")
                    if dedupe_key:
                        WebhookEventDedupes.mark_failed(channel.type, dedupe_key)
                    raise HTTPException(status_code=429, detail="overloaded")
                _inflight += 1
            background_tasks.add_task(
                _send_when_agent_done,
                agent_task,
                channel.id,
                incoming.reply_handle,
                dedupe_key,
            )
            return JSONResponse(content=adapter.async_ack_body)
        except Exception:
            log.exception("Sync agent path failed channel=%s", channel.id)
            if dedupe_key:
                WebhookEventDedupes.mark_failed(channel.type, dedupe_key)
            # Return the user-facing fallback inline rather than a 500 —
            # a 500 makes the platform retry, and our two-phase dedupe
            # would block the retry, leaving the user with no reply at
            # all. Better to apologize once and call it done.
            try:
                fallback = adapter.format_sync_reply(
                    OutgoingMessage(text=DEFAULT_ERROR_REPLY), incoming.reply_handle
                )
                if isinstance(fallback, Response):
                    return fallback
                return JSONResponse(content=fallback)
            except Exception:
                log.exception("format_sync_reply failed for fallback channel=%s", channel.id)
                raise HTTPException(status_code=500, detail="agent failure")
        chunks = _prepare_outgoing(adapter, out)
        primary = chunks[0]
        try:
            response_body = adapter.format_sync_reply(primary, incoming.reply_handle)
        except Exception:
            log.exception("format_sync_reply failed channel=%s", channel.id)
            if dedupe_key:
                WebhookEventDedupes.mark_failed(channel.type, dedupe_key)
            raise HTTPException(status_code=500, detail="reply formatting failed")
        # Sync mode allows only one inline reply; if the agent returned a
        # long answer that needed chunking, send the remainder out-of-band
        # so we don't lose it.
        for extra in chunks[1:]:
            background_tasks.add_task(_safe_send, adapter, channel.id, incoming.reply_handle, extra)
        if dedupe_key:
            WebhookEventDedupes.mark_done(channel.type, dedupe_key)
        if isinstance(response_body, Response):
            return response_body
        return JSONResponse(content=response_body)

    # async path
    async with _inflight_lock:
        if _inflight >= _MAX_INFLIGHT:
            log.warning("Async inflight cap reached (%d); shedding load", _inflight)
            if dedupe_key:
                WebhookEventDedupes.mark_failed(channel.type, dedupe_key)
            raise HTTPException(status_code=429, detail="overloaded")
        _inflight += 1

    background_tasks.add_task(
        _run_agent_and_send,
        channel.id,
        bot_id,
        user_id,
        session_id,
        incoming.text,
        incoming.reply_handle,
        dedupe_key,
    )
    return JSONResponse(content=adapter.async_ack_body)


async def _run_agent(bot_id: str, user_id: str, session_id: str, text: str, channel_id: Optional[str] = None) -> OutgoingMessage:
    """Call the sync ``AgentService.process_message`` from async context.

    Phase 3 backend-backfill: thread the resolved ``channel_id`` so message
    persistence records per-message channel attribution. The call sites at
    lines 456/460 below pass ``channel.id`` from the resolved BotChannel
    row.
    """
    from utils.dependencies import get_agent_service

    agent_service = get_agent_service()

    def _call() -> Tuple[str, list]:
        result = agent_service.process_message(
            bot_id=bot_id,
            user_id=user_id,
            session_id=session_id,
            page_id="channel",
            message=text,
            stream=False,
            channel_id=channel_id,
        )
        if isinstance(result, tuple):
            return result  # (text, citations)
        return (str(result), [])

    text_out, citations = await run_in_threadpool(_call)
    return OutgoingMessage(text=text_out, citations=citations or [])


async def _safe_send(
    adapter_or_type, channel_id: str, reply_handle: Any, out: OutgoingMessage
) -> None:
    if isinstance(adapter_or_type, str):
        adapter = registry_get(adapter_or_type)
    else:
        adapter = adapter_or_type
    if adapter is None:
        return
    channel = BotChannels.get_by_id(channel_id)
    if channel is None:
        return
    try:
        await send_with_retry(adapter, channel, reply_handle, out)
    except Exception:
        log.exception("Suppressed send failure channel=%s type=%s", channel.id, adapter.type)


async def _run_agent_and_send(
    channel_id: str,
    bot_id: str,
    user_id: str,
    session_id: str,
    text: str,
    reply_handle: Any,
    dedupe_key: Optional[str],
) -> None:
    global _inflight
    try:
        channel = BotChannels.get_by_id(channel_id)
        if channel is None or not channel.is_active:
            return
        adapter = registry_get(channel.type)
        if adapter is None:
            return
        try:
            out = await _run_agent(bot_id, user_id, session_id, text, channel_id=channel_id)
        except Exception:
            log.exception("Agent run failed channel=%s", channel_id)
            try:
                await send_with_retry(
                    adapter, channel, reply_handle, OutgoingMessage(text=DEFAULT_ERROR_REPLY)
                )
            except Exception:
                log.exception("Error-fallback send also failed channel=%s", channel_id)
            if dedupe_key:
                WebhookEventDedupes.mark_failed(channel.type, dedupe_key)
            return

        chunks = _prepare_outgoing(adapter, out)
        for chunk in chunks:
            try:
                await send_with_retry(adapter, channel, reply_handle, chunk)
            except Exception:
                log.exception("Send failed channel=%s", channel_id)
                if dedupe_key:
                    WebhookEventDedupes.mark_failed(channel.type, dedupe_key)
                return
        if dedupe_key:
            WebhookEventDedupes.mark_done(channel.type, dedupe_key)
    finally:
        async with _inflight_lock:
            _inflight = max(0, _inflight - 1)


async def _send_when_agent_done(
    agent_task: "asyncio.Task[OutgoingMessage]",
    channel_id: str,
    reply_handle: Any,
    dedupe_key: Optional[str],
) -> None:
    """Sync-with-timeout fallback: the request handler already started
    the agent and we ack'd the webhook when its sync window elapsed.
    Wait for the existing task to finish, then deliver the reply via the
    platform's REST API."""
    global _inflight
    try:
        channel = BotChannels.get_by_id(channel_id)
        if channel is None or not channel.is_active:
            agent_task.cancel()
            return
        adapter = registry_get(channel.type)
        if adapter is None:
            agent_task.cancel()
            return
        try:
            out = await agent_task
        except Exception:
            log.exception("Agent run failed (post-timeout) channel=%s", channel_id)
            try:
                await send_with_retry(
                    adapter, channel, reply_handle, OutgoingMessage(text=DEFAULT_ERROR_REPLY)
                )
            except Exception:
                log.exception("Error-fallback send also failed channel=%s", channel_id)
            if dedupe_key:
                WebhookEventDedupes.mark_failed(channel.type, dedupe_key)
            return

        chunks = _prepare_outgoing(adapter, out)
        for chunk in chunks:
            try:
                await send_with_retry(adapter, channel, reply_handle, chunk)
            except Exception:
                log.exception("Send failed (post-timeout) channel=%s", channel_id)
                if dedupe_key:
                    WebhookEventDedupes.mark_failed(channel.type, dedupe_key)
                return
        if dedupe_key:
            WebhookEventDedupes.mark_done(channel.type, dedupe_key)
    finally:
        async with _inflight_lock:
            _inflight = max(0, _inflight - 1)
