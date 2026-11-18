"""Backward-compatible re-exports — prefer ``services.deepgram``. """
from services.deepgram import (  # noqa: F401
    DEFAULT_TTS_MODEL as DEFAULT_MODEL,
    MAX_TTS_CHARS as MAX_CHARS,
    deepgram_configured,
    deployment_api_key as deepgram_api_key,
    resolve_tts_model as resolve_model,
    synthesize,
)
