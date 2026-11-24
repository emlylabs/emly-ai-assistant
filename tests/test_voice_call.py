"""Voice calls (services/voice_call.py): speech text filtering, the answer
processor's frames and persistence guarantee, and the wire serializer.

Runs Pipecat processors with Pipecat's own test harness; no network."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest

from services import voice_call as vc
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InputTransportMessageFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import SleepFrame, run_test


# ---- SpeechFilter -----------------------------------------------------------


def speak_all(chunks):
    f = vc.SpeechFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_plain_text_passes_through():
    assert speak_all(["Hello ", "there."]) == "Hello there."


def test_internal_code_is_dropped_even_when_split_across_chunks():
    chunks = ["Sure, let me ", "open that. <inter", "nal_code>BOOK", "_DEMO</internal", "_code> Done."]
    assert speak_all(chunks) == "Sure, let me open that.  Done."


def test_a_lone_angle_bracket_is_still_spoken():
    assert speak_all(["a < b", " and c"]) == "a < b and c"


def test_structured_answers_stay_silent():
    assert speak_all(["  [", '{"name": "x"}]']) == ""
    assert speak_all(['{"a": 1}']) == ""


def test_unclosed_code_is_not_spoken():
    assert speak_all(["Hi <internal_code>X"]) == "Hi "


# ---- AnswerProcessor --------------------------------------------------------


def context_with(text):
    ctx = LLMContext()
    ctx.add_message({"role": "user", "content": text})
    return ctx


def messages(frames, kind=None):
    out = [f.message for f in frames if isinstance(f, OutputTransportMessageUrgentFrame)]
    return [m for m in out if kind is None or m["type"] == kind]


def test_answer_streams_text_to_tts_and_chat():
    seen = []

    def answer(text):
        seen.append(text)
        yield {"type": "token", "data": "Our hours "}
        yield {"type": "token", "data": "are 9 to 5.<internal_code>F1</internal_code>"}
        yield {"type": "citations", "data": [{"title": "Hours"}], "message_id": 42}

    proc = vc.AnswerProcessor(answer=answer, max_chars=100)
    down, _ = asyncio.run(run_test(
        proc,
        frames_to_send=[LLMContextFrame(context_with("/what are your hours")), SleepFrame(sleep=0.5)],
    ))
    # Leading slashes never reach the agent (they'd be read as commands).
    assert seen == ["what are your hours"]

    kinds = [type(f) for f in down]
    assert kinds.index(LLMFullResponseStartFrame) < kinds.index(LLMFullResponseEndFrame)
    spoken = "".join(f.text for f in down if isinstance(f, LLMTextFrame))
    assert spoken == "Our hours are 9 to 5."

    assert messages(down, "user_transcript") == [
        {"type": "user_transcript", "turn": 1, "text": "what are your hours"}]
    done = messages(down, "bot_done")[0]
    assert done["message_id"] == 42
    assert done["citations"] == [{"title": "Hours"}]
    # The chat bubble gets the raw text; the widget opens the form from it.
    assert done["text"] == "Our hours are 9 to 5.<internal_code>F1</internal_code>"


def test_transcript_is_capped():
    seen = []

    def answer(text):
        seen.append(text)
        yield {"type": "citations", "data": [], "message_id": None}

    proc = vc.AnswerProcessor(answer=answer, max_chars=10)
    asyncio.run(run_test(
        proc, frames_to_send=[LLMContextFrame(context_with("x" * 50)), SleepFrame(sleep=0.3)]))
    assert seen == ["x" * 10]


def test_non_user_context_is_ignored():
    calls = []

    def answer(text):
        calls.append(text)
        yield from ()

    ctx = LLMContext()
    ctx.add_message({"role": "developer", "content": "The user went quiet."})
    proc = vc.AnswerProcessor(answer=answer, max_chars=100)
    asyncio.run(run_test(proc, frames_to_send=[LLMContextFrame(ctx), SleepFrame(sleep=0.2)]))
    assert calls == []


def test_interrupted_answer_stops_speaking_but_is_still_drained():
    release = threading.Event()
    drained = threading.Event()

    def answer(text):
        yield {"type": "token", "data": "First sentence here. "}
        release.wait(timeout=5)
        yield {"type": "token", "data": "Second part, never spoken."}
        yield {"type": "citations", "data": [], "message_id": 7}
        drained.set()  # in the real agent, this is where the turn is saved

    proc = vc.AnswerProcessor(answer=answer, max_chars=100)

    async def scenario():
        async def unblock():
            await asyncio.sleep(0.3)
            release.set()

        task = asyncio.create_task(unblock())
        result = await run_test(
            proc,
            frames_to_send=[
                LLMContextFrame(context_with("hello")),
                SleepFrame(sleep=0.15),
                InterruptionFrame(),
                SleepFrame(sleep=0.6),
            ],
        )
        await task
        return result

    down, _ = asyncio.run(scenario())
    spoken = "".join(f.text for f in down if isinstance(f, LLMTextFrame))
    assert "never spoken" not in spoken
    assert drained.is_set()
    done = messages(down, "bot_done")[0]
    assert done["message_id"] == 7
    assert "never spoken" in done["text"]  # the bubble still shows the full answer


def test_answer_error_is_reported_to_the_widget():
    def answer(text):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    proc = vc.AnswerProcessor(answer=answer, max_chars=100)
    down, _ = asyncio.run(run_test(
        proc, frames_to_send=[LLMContextFrame(context_with("hi")), SleepFrame(sleep=0.3)]))
    assert messages(down, "bot_done")[0].get("error") is True


# ---- Serializer -------------------------------------------------------------


class _Setup:
    audio_in_sample_rate = vc.IN_SAMPLE_RATE


def make_serializer():
    s = vc.WidgetVoiceSerializer()
    asyncio.run(s.setup(_Setup()))
    return s


def test_binary_messages_become_mic_audio():
    s = make_serializer()
    frame = asyncio.run(s.deserialize(b"\x01\x00" * 160 + b"\x07"))
    assert isinstance(frame, InputAudioRawFrame)
    assert frame.sample_rate == vc.IN_SAMPLE_RATE and len(frame.audio) == 320


def test_typed_message_becomes_a_turn_frame():
    frame = asyncio.run(make_serializer().deserialize('{"type": "text", "text": "hello"}'))
    assert isinstance(frame, InputTransportMessageFrame)
    assert frame.message == {"type": "text", "text": "hello"}


def test_typed_message_is_answered_and_interrupts():
    seen = []

    def answer(text):
        seen.append(text)
        yield {"type": "token", "data": "Hi there."}
        yield {"type": "citations", "data": [], "message_id": 3}

    proc = vc.AnswerProcessor(answer=answer, max_chars=100)
    down, up = asyncio.run(run_test(
        proc,
        frames_to_send=[
            InputTransportMessageFrame(message={"type": "text", "text": "typed question"}),
            SleepFrame(sleep=0.4),
        ],
    ))
    assert seen == ["typed question"]
    assert any(isinstance(f, InterruptionFrame) for f in down)
    assert messages(down, "user_transcript")[0]["text"] == "typed question"
    assert messages(down, "bot_done")[0]["message_id"] == 3


@pytest.mark.parametrize("data", [b"", b"\x00" * (vc.MAX_AUDIO_MESSAGE_BYTES + 2), '{"type":"x"}',
                                  '{"type":"text","text":"  "}', "not json"])
def test_other_messages_are_ignored(data):
    assert asyncio.run(make_serializer().deserialize(data)) is None


def test_mic_audio_faster_than_real_time_is_dropped():
    s = make_serializer()
    frame = b"\x00\x00" * 1600  # 100 ms
    kept = sum(asyncio.run(s.deserialize(frame)) is not None for _ in range(40))  # 4 s at once
    assert kept == vc.MAX_AUDIO_BYTES_PER_SECOND // len(frame)  # 2 s worth


def test_outgoing_frames():
    s = make_serializer()
    audio = OutputAudioRawFrame(audio=b"\x00\x01" * 10, sample_rate=24000, num_channels=1)
    assert asyncio.run(s.serialize(audio)) == b"\x00\x01" * 10
    assert json.loads(asyncio.run(s.serialize(InterruptionFrame()))) == {"type": "interrupt"}
    event = OutputTransportMessageUrgentFrame(message={"type": "ready"})
    assert json.loads(asyncio.run(s.serialize(event))) == {"type": "ready"}


@pytest.mark.parametrize("voice,service", [
    ("flux-naveen-en", "DeepgramFluxTTSService"),
    ("aura-2-thalia-en", "DeepgramTTSService"),
])
def test_calls_use_the_chosen_voice(voice, service):
    tts = vc.make_tts("key", voice)
    assert type(tts).__name__ == service
    assert tts._settings.voice == voice


@pytest.mark.parametrize("tag,expected", [("es-MX", "es-MX"), ("fr-ZZ", "fr"), ("xx", "en"), (None, "en")])
def test_language_mapping(tag, expected):
    assert vc.deepgram_language(tag).value == expected
