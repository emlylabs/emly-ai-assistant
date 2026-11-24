"""Realtime voice calls for the web widget, built on open-source Pipecat.

Pipecat (BSD-2-Clause) runs inside this app; no hosted service is involved
apart from Deepgram for speech. One call is one WebSocket
(``/widget/{bot}/voice/ws`` in ``routes/widget.py``) and one pipeline:

    browser mic (16 kHz PCM) → Silero VAD + turn detection
      → Deepgram live STT → the bot's normal answer path (AgentService)
      → Deepgram streaming TTS → browser speaker (24 kHz PCM)

Talking over the bot interrupts it: queued audio is dropped here and the
widget is told to clear its playback buffer. Each turn goes through
``AgentService.process_message`` exactly like a typed message, so limits,
takeover, RAG, forms, citations and persistence all behave the same, and
the turn lands in the same session as the text chat.

Wire protocol (same socket):
  binary, both ways      raw 16-bit little-endian mono PCM
  text, widget → server  {"type": "auth", ...} first (see the route); then
                         {"type": "text", "text"}  a typed message, answered
                         like a spoken one (it interrupts the bot)
  text, server → widget  {"type": "ready"}
                         {"type": "user_interim", "text"}     live caption
                         {"type": "user_transcript", "turn", "text"}
                         {"type": "bot_text", "turn", "text"}  answer delta
                         {"type": "bot_done", "turn", "text", "message_id",
                          "citations"}
                         {"type": "interrupt"}  stop playing queued audio
                         {"type": "error", "code"}  before the server closes

Logging: Pipecat logs with loguru, which would bypass this app's log
redaction, and logs reply text at DEBUG. Only its warnings and errors are
kept, and they go through stdlib ``logging`` like everything else.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Iterator, Optional

log = logging.getLogger(__name__)


def _route_pipecat_logs() -> None:
    # Must run before pipecat is imported, so its import banner is dropped too.
    from loguru import logger as loguru_logger

    target = logging.getLogger("pipecat")

    def sink(message: Any) -> None:
        record = message.record
        level = logging.ERROR if record["level"].no >= 40 else logging.WARNING
        target.log(level, "%s", record["message"])

    loguru_logger.remove()
    loguru_logger.add(sink, level="WARNING", format="{message}")


_route_pipecat_logs()

from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    AudioRawFrame,
    FatalErrorFrame,
    Frame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import (  # noqa: E402
    PipelineParams,
    PipelineWorker,
    ProcessorUnusablePolicy,
)
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import (  # noqa: E402
    LLMUserAggregator,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.serializers.base_serializer import FrameSerializer  # noqa: E402
from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService  # noqa: E402
from pipecat.services.deepgram.stt import DeepgramSTTService  # noqa: E402
from pipecat.services.deepgram.tts import DeepgramTTSService  # noqa: E402
from pipecat.transcriptions.language import Language  # noqa: E402
from pipecat.transports.websocket.fastapi import (  # noqa: E402
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy  # noqa: E402
from pipecat.turns.user_turn_strategies import UserTurnStrategies  # noqa: E402
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

IN_SAMPLE_RATE = 16000
OUT_SAMPLE_RATE = 24000
# One binary message from the widget is ~40-100 ms of audio; anything far
# larger is not a mic frame.
MAX_AUDIO_MESSAGE_BYTES = 64 * 1024
# Mic audio faster than real time only costs Deepgram time; allow 2x for
# network bursts and drop the rest.
MAX_AUDIO_BYTES_PER_SECOND = IN_SAMPLE_RATE * 2 * 2
MAX_CALL_SECONDS = int(os.environ.get("VOICE_CALL_MAX_SECONDS", "900"))
# Voice calls run in this process; cap them so text chat stays responsive.
MAX_CONCURRENT_CALLS = int(os.environ.get("VOICE_CALL_MAX_CONCURRENT", "10"))
# Nobody spoke and nothing was said for this long: hang up.
IDLE_TIMEOUT_SECONDS = 120
PAGE_ID = "voice"

_active_calls = 0


def at_capacity() -> bool:
    return _active_calls >= MAX_CONCURRENT_CALLS


def make_tts(api_key: str, voice: str) -> FrameProcessor:
    """Deepgram streaming TTS for the bot's chosen voice: Flux voices
    (``flux-*``) on ``wss://…/v2/speak``, Aura voices on ``/v1/speak``."""
    if voice.startswith("flux-"):
        return DeepgramFluxTTSService(
            api_key=api_key,
            sample_rate=OUT_SAMPLE_RATE,
            settings=DeepgramFluxTTSService.Settings(voice=voice),
            text_filters=[MarkdownTextFilter()],
        )
    return DeepgramTTSService(
        api_key=api_key,
        sample_rate=OUT_SAMPLE_RATE,
        settings=DeepgramTTSService.Settings(voice=voice),
        text_filters=[MarkdownTextFilter()],
    )


def deepgram_language(tag: Optional[str]) -> Language:
    """BCP-47 tag → Pipecat language, falling back to the base language,
    then English."""
    for candidate in (tag, (tag or "").split("-")[0]):
        if not candidate:
            continue
        try:
            return Language(candidate)
        except ValueError:
            continue
    return Language.EN


# ---- Wire format -----------------------------------------------------------


class WidgetVoiceSerializer(FrameSerializer):
    """Binary messages are PCM audio; text messages are JSON events."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._window_start = 0.0
        self._window_bytes = 0

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, InterruptionFrame):
            await self._reset_audio_out()
            return json.dumps({"type": "interrupt"})
        if isinstance(frame, AudioRawFrame):
            data = await self._resample_audio_out(frame)
            return data or None
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            return json.dumps(frame.message)
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if not isinstance(data, (bytes, bytearray)):
            return _typed_message(data)
        if not data or len(data) > MAX_AUDIO_MESSAGE_BYTES:
            return None
        now = time.monotonic()
        if now - self._window_start >= 1.0:
            self._window_start = now
            self._window_bytes = 0
        self._window_bytes += len(data)
        if self._window_bytes > MAX_AUDIO_BYTES_PER_SECOND:
            return None
        audio = bytes(data[: len(data) - (len(data) % 2)])
        return InputAudioRawFrame(audio=audio, num_channels=1, sample_rate=self._sample_rate)


def _typed_message(data: str) -> Frame | None:
    """``{"type": "text", "text": ...}`` → a typed turn; anything else is
    dropped."""
    if len(data) > 32 * 1024:
        return None
    try:
        message = json.loads(data)
    except ValueError:
        return None
    if not isinstance(message, dict) or message.get("type") != "text":
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return InputTransportMessageFrame(message={"type": "text", "text": text})


def _event(message: dict) -> OutputTransportMessageUrgentFrame:
    """Events for the chat UI skip the audio queue so text shows at once."""
    return OutputTransportMessageUrgentFrame(message=message)


# ---- Speech text -----------------------------------------------------------

_CODE_OPEN = "<internal_code>"
_CODE_CLOSE = "</internal_code>"


def _partial_tag_suffix(text: str, tag: str) -> int:
    """Length of the longest end of ``text`` that could start ``tag``."""
    for size in range(min(len(text), len(tag) - 1), 0, -1):
        if text.endswith(tag[:size]):
            return size
    return 0


class SpeechFilter:
    """Turns streamed answer chunks into text to speak.

    Drops ``<internal_code>…</internal_code>`` form triggers (the widget
    opens the form instead) even when a tag is split across chunks, and
    keeps structured answers (JSON lists or objects) silent; those show as
    cards in the chat."""

    def __init__(self) -> None:
        self._buf = ""
        self._in_code = False
        self._decided = False
        self.muted = False

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        if not self._decided:
            head = self._buf.lstrip()
            if not head:
                return ""
            self._decided = True
            self.muted = head[0] in "[{"
        out = []
        while True:
            if self._in_code:
                end = self._buf.find(_CODE_CLOSE)
                if end < 0:
                    keep = len(_CODE_CLOSE) - 1
                    self._buf = self._buf[-keep:] if len(self._buf) > keep else self._buf
                    break
                self._buf = self._buf[end + len(_CODE_CLOSE):]
                self._in_code = False
                continue
            start = self._buf.find(_CODE_OPEN)
            if start >= 0:
                out.append(self._buf[:start])
                self._buf = self._buf[start + len(_CODE_OPEN):]
                self._in_code = True
                continue
            hold = _partial_tag_suffix(self._buf, _CODE_OPEN)
            out.append(self._buf[: len(self._buf) - hold])
            self._buf = self._buf[len(self._buf) - hold:]
            break
        text = "".join(out)
        return "" if self.muted else text

    def flush(self) -> str:
        rest = "" if self._in_code else self._buf
        self._buf = ""
        return "" if self.muted else rest


# ---- Processors -------------------------------------------------------------


class TranscriptTap(FrameProcessor):
    """Sends live captions to the widget while the visitor talks."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterimTranscriptionFrame) and frame.text.strip():
            await self.push_frame(_event({"type": "user_interim", "text": frame.text[:500]}))
        await self.push_frame(frame, direction)


AnswerFn = Callable[[str], Iterator[dict]]


class AnswerProcessor(FrameProcessor):
    """Stands in for an LLM service: answers each user turn with the bot's
    normal answer path and streams the text to TTS and to the chat UI.

    ``answer`` is synchronous and blocking (it is the same generator the
    text chat streams), so it runs in a thread. The turn is saved only when
    that generator is drained, so it is always drained, even when the
    visitor interrupts; the interruption only stops the speaking."""

    def __init__(self, *, answer: AnswerFn, max_chars: int, **kwargs):
        super().__init__(**kwargs)
        self._answer = answer
        self._max_chars = max_chars
        self._turn = 0
        self._speaking_turn: Optional[int] = None
        # The answer path keeps per-session state; one turn at a time.
        self._lock = asyncio.Lock()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            text = _last_user_text(frame.context)[: self._max_chars].lstrip("/").strip()
            if text:
                self._turn += 1
                self.create_task(self._run_turn(self._turn, text), name=f"voice-turn-{self._turn}")
            return
        if (
            isinstance(frame, InputTransportMessageFrame)
            and direction == FrameDirection.DOWNSTREAM
            and isinstance(frame.message, dict)
            and frame.message.get("type") == "text"
        ):
            text = str(frame.message.get("text") or "")[: self._max_chars].lstrip("/").strip()
            if text:
                # Typing over the bot interrupts it, as talking does.
                self._speaking_turn = None
                await self.broadcast_interruption()
                self._turn += 1
                self.create_task(self._run_turn(self._turn, text), name=f"voice-turn-{self._turn}")
            return
        if isinstance(frame, InterruptionFrame):
            self._speaking_turn = None
        await self.push_frame(frame, direction)

    async def _run_turn(self, turn: int, text: str) -> None:
        await self.push_frame(_event({"type": "user_transcript", "turn": turn, "text": text}))
        async with self._lock:
            # A newer turn arrived while this one waited: answer (and save)
            # it, but only speak the newest.
            self._speaking_turn = turn if turn == self._turn else None
            queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def put(item: Any) -> None:
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, item)
                except RuntimeError:
                    pass  # the call ended; keep draining so the turn is saved

            def drain() -> None:
                try:
                    for event in self._answer(text):
                        put(event)
                except Exception as exc:
                    log.warning("voice answer failed: %s", type(exc).__name__)
                    put({"type": "error"})
                finally:
                    put(None)

            threading.Thread(target=drain, name="voice-answer", daemon=True).start()

            speech = SpeechFilter()
            reply: list[str] = []
            started = False
            message_id = None
            citations: list = []
            failed = False
            while True:
                event = await queue.get()
                if event is None:
                    break
                kind = event.get("type")
                if kind == "token":
                    chunk = str(event.get("data") or "")
                    if not chunk:
                        continue
                    reply.append(chunk)
                    await self.push_frame(_event({"type": "bot_text", "turn": turn, "text": chunk}))
                    spoken = speech.feed(chunk)
                    if spoken and self._speaking_turn == turn:
                        if not started:
                            started = True
                            await self.push_frame(LLMFullResponseStartFrame())
                        await self.push_frame(LLMTextFrame(spoken))
                elif kind == "citations":
                    message_id = event.get("message_id")
                    citations = event.get("data") or []
                elif kind == "error":
                    failed = True

            if self._speaking_turn == turn:
                tail = speech.flush()
                if tail.strip():
                    if not started:
                        started = True
                        await self.push_frame(LLMFullResponseStartFrame())
                    await self.push_frame(LLMTextFrame(tail))
            if started:
                # Flushes the last sentence to TTS.
                await self.push_frame(LLMFullResponseEndFrame())
            await self.push_frame(_event({
                "type": "bot_done",
                "turn": turn,
                "text": "".join(reply),
                "message_id": message_id,
                "citations": citations,
                **({"error": True} if failed else {}),
            }))


def _last_user_text(context: LLMContext) -> str:
    messages = getattr(context, "messages", None) or []
    if not messages:
        return ""
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        return ""
    content = last.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # [{"type": "text", "text": ...}, ...]
        return " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


# ---- One call ---------------------------------------------------------------


async def run_call(
    websocket: Any,
    *,
    api_key: str,
    stt_model: str,
    tts_voice: str,
    language: Optional[str],
    answer: AnswerFn,
    max_chars: int,
) -> None:
    """Run one voice call on an accepted, authenticated WebSocket until the
    visitor hangs up, the call goes idle, or it hits ``MAX_CALL_SECONDS``."""
    global _active_calls
    _active_calls += 1
    try:
        await _run_pipeline(
            websocket,
            api_key=api_key,
            stt_model=stt_model,
            tts_voice=tts_voice,
            language=language,
            answer=answer,
            max_chars=max_chars,
        )
    finally:
        _active_calls -= 1


async def _send_json(websocket: Any, message: dict) -> None:
    try:
        await websocket.send_text(json.dumps(message))
    except Exception:
        pass


async def _send_error(websocket: Any, code: str) -> None:
    await _send_json(websocket, {"type": "error", "code": code})


async def _run_pipeline(
    websocket: Any,
    *,
    api_key: str,
    stt_model: str,
    tts_voice: str,
    language: Optional[str],
    answer: AnswerFn,
    max_chars: int,
) -> None:
    lang = deepgram_language(language)
    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=IN_SAMPLE_RATE,
            audio_out_sample_rate=OUT_SAMPLE_RATE,
            serializer=WidgetVoiceSerializer(),
            # The route checks Origin against the bot's allowlist itself.
            allowed_origins=[],
        ),
    )
    stt = DeepgramSTTService(
        api_key=api_key,
        settings=DeepgramSTTService.Settings(
            model=stt_model,
            language=lang,
            interim_results=True,
            punctuate=True,
            smart_format=True,
        ),
    )
    tts = make_tts(api_key, tts_voice)
    user_turns = LLMUserAggregator(
        LLMContext(),
        params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(sample_rate=IN_SAMPLE_RATE),
            # A turn ends shortly after the visitor stops talking.
            user_turn_strategies=UserTurnStrategies(stop=[SpeechTimeoutUserTurnStopStrategy()]),
            empty_user_turn=None,
        ),
    )
    pipeline = Pipeline([
        transport.input(),
        stt,
        TranscriptTap(),
        user_turns,
        AnswerProcessor(answer=answer, max_chars=max_chars),
        tts,
        transport.output(),
    ])
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=IN_SAMPLE_RATE,
            audio_out_sample_rate=OUT_SAMPLE_RATE,
        ),
        # RTVI is Pipecat's client protocol; this widget has its own.
        enable_rtvi=False,
        idle_timeout_secs=IDLE_TIMEOUT_SECONDS,
        # A speech service that can't work (bad key, quota) ends the call.
        processor_unusable_policy=ProcessorUnusablePolicy.CANCEL,
    )

    @worker.event_handler("on_pipeline_started")
    async def _on_started(_worker, _frame):
        # Only now has the start reached every processor; a frame queued
        # earlier (e.g. from on_client_connected) is dropped by the output.
        try:
            await websocket.send_text(json.dumps({"type": "ready"}))
        except Exception:
            pass

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport, _ws):
        await worker.cancel(reason="client disconnected")

    @worker.event_handler("on_pipeline_error")
    async def _on_error(_worker, frame):
        # Message is secret-redacted by Pipecat; still log the type only.
        log.warning("voice call pipeline error: %s", type(frame).__name__)
        processor = getattr(frame, "processor", None)
        unusable = processor is not None and not getattr(processor, "is_usable", True)
        if isinstance(frame, FatalErrorFrame) or unusable:
            # The worker cancels the call next (ProcessorUnusablePolicy.CANCEL).
            await _send_error(websocket, "speech_service_error")

    @worker.event_handler("on_idle_timeout")
    async def _on_idle(_worker):
        await _send_error(websocket, "idle_timeout")

    async def _cap_duration() -> None:
        await asyncio.sleep(MAX_CALL_SECONDS)
        await _send_error(websocket, "call_too_long")
        await worker.cancel(reason="max call length")

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    cap = asyncio.create_task(_cap_duration())
    try:
        await runner.run()
    finally:
        cap.cancel()
