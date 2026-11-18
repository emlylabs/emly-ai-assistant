"""Deepgram STT + TTS for the embeddable widget.

API key resolution (first match wins):
1. Per-bot encrypted key (``bots.deepgram_api_key_encrypted``)
2. Deployment ``DEEPGRAM_API_KEY`` / ``DEEPGRAM_API_KEY_FILE``

The widget never sees the key — it posts audio/text to
``/widget/{bot}/stt`` and ``/widget/{bot}/tts``.
"""
from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import urlencode

import httpx

from models.bots import Bots
from services.secrets import decrypt, encrypt

log = logging.getLogger(__name__)

DEFAULT_TTS_MODEL = "aura-2-thalia-en"
DEFAULT_STT_MODEL = "nova-3"
MAX_TTS_CHARS = 2000
MAX_STT_BYTES = 10 * 1024 * 1024  # 10 MB
_TIMEOUT = httpx.Timeout(45.0, connect=10.0)


def deployment_api_key() -> Optional[str]:
    key = (os.environ.get("DEEPGRAM_API_KEY") or "").strip()
    return key or None


def get_bot_api_key(bot_id: str) -> Optional[str]:
    bot = Bots.get_by_id(bot_id)
    if not bot or not bot.deepgram_api_key_encrypted:
        return None
    return decrypt(bot.deepgram_api_key_encrypted)


def set_bot_api_key(bot_id: str, api_key: Optional[str]) -> bool:
    """Store or clear the per-bot Deepgram key. Empty string clears."""
    ciphertext = encrypt(api_key) if (api_key or "").strip() else None
    return Bots.update_deepgram_api_key(bot_id, ciphertext)


def resolve_api_key(bot_id: Optional[str] = None) -> Optional[str]:
    if bot_id:
        key = get_bot_api_key(bot_id)
        if key:
            return key
    return deployment_api_key()


def deepgram_configured(bot_id: Optional[str] = None) -> bool:
    return resolve_api_key(bot_id) is not None


def resolve_tts_model(configured: Optional[str] = None) -> str:
    explicit = (configured or "").strip()
    if explicit:
        return explicit
    return (os.environ.get("DEEPGRAM_TTS_MODEL") or "").strip() or DEFAULT_TTS_MODEL


def resolve_stt_model(configured: Optional[str] = None) -> str:
    explicit = (configured or "").strip()
    if explicit:
        return explicit
    return (os.environ.get("DEEPGRAM_STT_MODEL") or "").strip() or DEFAULT_STT_MODEL


def synthesize(
    text: str,
    *,
    bot_id: Optional[str] = None,
    model: Optional[str] = None,
) -> bytes:
    """Call Deepgram Speak and return MP3 bytes."""
    key = resolve_api_key(bot_id)
    if not key:
        raise RuntimeError("Deepgram API key is not configured")

    plain = (text or "").strip()
    if not plain:
        raise RuntimeError("empty text")
    if len(plain) > MAX_TTS_CHARS:
        plain = plain[:MAX_TTS_CHARS].rstrip() + "…"

    params = urlencode({"model": resolve_tts_model(model), "encoding": "mp3"})
    url = f"https://api.deepgram.com/v1/speak?{params}"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Token {key}",
                    "Content-Type": "application/json",
                },
                json={"text": plain},
            )
    except httpx.HTTPError as exc:
        log.warning("Deepgram TTS network error: %s", type(exc).__name__)
        raise RuntimeError("Deepgram TTS request failed") from exc

    if resp.status_code >= 400:
        log.warning("Deepgram TTS HTTP %s", resp.status_code)
        raise RuntimeError(f"Deepgram TTS failed ({resp.status_code})")

    body = resp.content or b""
    if not body:
        raise RuntimeError("Deepgram TTS returned empty audio")
    return body


def transcribe(
    audio: bytes,
    *,
    content_type: str = "audio/webm",
    bot_id: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Transcribe pre-recorded audio via Deepgram Listen. Returns plain text."""
    key = resolve_api_key(bot_id)
    if not key:
        raise RuntimeError("Deepgram API key is not configured")
    if not audio:
        raise RuntimeError("empty audio")
    if len(audio) > MAX_STT_BYTES:
        raise RuntimeError("audio too large")

    params = urlencode({
        "model": resolve_stt_model(model),
        "smart_format": "true",
        "punctuate": "true",
    })
    url = f"https://api.deepgram.com/v1/listen?{params}"
    ctype = (content_type or "audio/webm").split(";")[0].strip() or "audio/webm"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Token {key}",
                    "Content-Type": ctype,
                },
                content=audio,
            )
    except httpx.HTTPError as exc:
        log.warning("Deepgram STT network error: %s", type(exc).__name__)
        raise RuntimeError("Deepgram STT request failed") from exc

    if resp.status_code >= 400:
        log.warning("Deepgram STT HTTP %s", resp.status_code)
        raise RuntimeError(f"Deepgram STT failed ({resp.status_code})")

    try:
        data = resp.json()
        transcript = (
            data.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("transcript", "")
        )
    except (TypeError, ValueError, IndexError, KeyError, AttributeError) as exc:
        raise RuntimeError("Deepgram STT returned unexpected JSON") from exc

    text = (transcript or "").strip()
    if not text:
        raise RuntimeError("No speech detected")
    return text
