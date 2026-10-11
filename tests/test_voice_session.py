from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Sequence
from typing import Literal

import numpy as np

from friday.agent.llm import CallInfo, LLMError, Usage
from friday.agent.loop import RunResult, SessionTurn
from friday.voice import VoiceCall, VoiceSession
from friday.voice.microphone import FRAME_SAMPLES, SAMPLE_RATE, Pcm
from friday.voice.speech_to_text import pcm_to_wav
from friday.voice.text_to_speech import tone

INFO = CallInfo(Usage(10, 5, 0), "fake-audio-model", 0.5)


def frame(amplitude: float) -> Pcm:
    t = np.arange(FRAME_SAMPLES) / SAMPLE_RATE
    return (np.sin(2 * np.pi * 440 * t) * amplitude).astype(np.int16)


QUIET, LOUD = frame(20), frame(4000)
SPEECH = [LOUD] * 20 + [QUIET] * 40


class FakeMic:
    def __init__(self, frames: list[Pcm]) -> None:
        self.frames = deque(frames)
        self.paused = False

    async def read(self, wait_s: float) -> Pcm | None:
        await asyncio.sleep(0)
        return self.frames.popleft() if self.frames else None

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False


class FakeWake:
    def __init__(self, fire_on_frame: int) -> None:
        self.fed = 0
        self.fire_on = fire_on_frame
        self.resets = 0

    def feed(self, frame: Pcm) -> bool:
        self.fed += 1
        return self.fed == self.fire_on

    def reset(self) -> None:
        self.resets += 1


class FakeSpeaker:
    def __init__(self, interrupt_playback: bool = False) -> None:
        self.beeps: list[str] = []
        self.played = 0
        self.interrupt_playback = interrupt_playback

    async def play(self, pcm: Pcm, rate: int, interrupt: asyncio.Event) -> bool:
        self.played += 1
        if self.interrupt_playback:
            interrupt.set()  # the user pressed the hotkey while Friday was talking
        return not interrupt.is_set()

    async def play_stream(
        self, chunks: AsyncIterator[bytes], rate: int, interrupt: asyncio.Event
    ) -> bool:
        self.streamed = getattr(self, "streamed", 0) + 1
        received = b""
        async for data in chunks:
            received += data
            if self.interrupt_playback:
                interrupt.set()
                return False
        self.streamed_bytes = len(received)
        return True

    async def beep(self, kind: Literal["listen", "done", "error"]) -> None:
        self.beeps.append(kind)


class FakeSpeechStream:
    sample_rate = 24_000
    first_audio_s = 0.4

    def __init__(self, parts: list[bytes]) -> None:
        self._parts = parts

    @property
    def info(self) -> CallInfo:
        return INFO

    async def chunks(self) -> AsyncIterator[bytes]:
        for part in self._parts:
            yield part


class RecorderUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.heard_text: list[str] = []
        self.errors: list[str] = []
        self.results: list[RunResult] = []
        self.voice_calls: list[list[VoiceCall]] = []

    def status(self, message: str) -> None:
        self.messages.append(message)

    def heard(self, text: str) -> None:
        self.heard_text.append(text)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def result(self, result: RunResult, calls: list[VoiceCall]) -> None:
        self.results.append(result)

    def voice_metrics(self, calls: list[VoiceCall]) -> None:
        self.voice_calls.append(list(calls))


def make_result(summary: str = "Done.") -> RunResult:
    return RunResult("success", summary, "r1", 1, 1, Usage(), 0.1)


def build(
    frames: list[Pcm],
    *,
    transcript: str | Exception = "hey friday open notepad",
    wake: FakeWake | None = None,
    hotkey: asyncio.Event | None = None,
    speaker: FakeSpeaker | None = None,
    agent_error: Exception | None = None,
    stream: FakeSpeechStream | Exception | None = None,
) -> tuple[VoiceSession, RecorderUI, FakeSpeaker, list[tuple[str, Sequence[SessionTurn]]]]:
    ui, speaker = RecorderUI(), speaker or FakeSpeaker()
    agent_calls: list[tuple[str, Sequence[SessionTurn]]] = []

    async def transcribe(wav: bytes) -> tuple[str, CallInfo]:
        if isinstance(transcript, Exception):
            raise transcript
        return transcript, INFO

    async def synthesize(text: str) -> tuple[bytes, CallInfo]:
        return pcm_to_wav(tone(440, 0.1), 24_000), INFO

    async def synthesize_stream(text: str) -> FakeSpeechStream:
        if isinstance(stream, Exception):
            raise stream
        assert stream is not None
        return stream

    async def run_agent(text: str, session: Sequence[SessionTurn]) -> RunResult:
        agent_calls.append((text, session))
        if agent_error:
            raise agent_error
        return make_result()

    session = VoiceSession(
        mic=FakeMic(frames),
        wake=wake,
        hotkey=hotkey or asyncio.Event(),
        speaker=speaker,
        transcribe=transcribe,
        synthesize=synthesize,
        synthesize_stream=synthesize_stream if stream is not None else None,
        run_agent=run_agent,
        ui=ui,
    )
    return session, ui, speaker, agent_calls


async def test_wake_word_to_spoken_answer_end_to_end() -> None:
    session, ui, speaker, calls = build([QUIET] * 3 + SPEECH, wake=FakeWake(fire_on_frame=3))
    result = await session.run_once()
    assert result is not None and result.summary == "Done."
    assert calls == [("open notepad", ())]  # the wake phrase was stripped
    assert ui.heard_text == ["open notepad"] and speaker.beeps == ["listen"]
    assert speaker.played == 1  # the reply was spoken
    assert [c.kind for c in ui.voice_calls[0]] == ["stt", "tts"]
    assert ui.voice_calls[0][0].detail.endswith("of audio")


async def test_hotkey_alone_starts_a_conversation_without_a_wake_detector() -> None:
    hotkey = asyncio.Event()
    hotkey.set()
    session, _, _, calls = build(SPEECH, hotkey=hotkey, wake=None)
    assert await session.run_once() is not None
    assert len(calls) == 1 and not hotkey.is_set()


async def test_silence_after_the_trigger_does_nothing() -> None:
    hotkey = asyncio.Event()
    hotkey.set()
    session, ui, _, calls = build([QUIET] * 300, hotkey=hotkey)
    assert await session.run_once() is None
    assert calls == [] and "I didn't hear anything." in ui.messages


async def test_an_empty_transcript_does_not_run_the_agent() -> None:
    hotkey = asyncio.Event()
    hotkey.set()
    session, ui, _, calls = build(SPEECH, hotkey=hotkey, transcript="hey friday")
    assert await session.run_once() is None
    assert calls == [] and "I didn't catch that." in ui.messages


async def test_speech_recognition_failure_is_reported_and_survivable() -> None:
    hotkey = asyncio.Event()
    hotkey.set()
    session, ui, speaker, calls = build(SPEECH, hotkey=hotkey, transcript=LLMError("down"))
    assert await session.run_once() is None
    assert calls == [] and "Speech recognition failed" in ui.errors[0]
    assert speaker.beeps == ["listen", "error"]


async def test_an_agent_crash_does_not_kill_the_voice_loop() -> None:
    hotkey = asyncio.Event()
    hotkey.set()
    session, ui, _, _ = build(SPEECH, hotkey=hotkey, agent_error=RuntimeError("boom"))
    assert await session.run_once() is None
    assert "RuntimeError: boom" in ui.errors[0]


async def test_the_hotkey_interrupts_speech_and_immediately_starts_listening_again() -> None:
    hotkey = asyncio.Event()
    hotkey.set()
    session, _, _, _ = build(SPEECH, hotkey=hotkey, speaker=FakeSpeaker(interrupt_playback=True))
    await session.run_once()
    assert hotkey.is_set()  # the press that cut the reply short is still pending
    assert await session._wait_for_trigger() == "hotkey"  # pyright: ignore[reportPrivateUsage]


async def test_follow_up_requests_remember_the_previous_task() -> None:
    hotkey = asyncio.Event()
    session, _, _, calls = build(SPEECH + SPEECH, hotkey=hotkey)
    hotkey.set()
    await session.run_once()
    hotkey.set()
    await session.run_once()
    assert calls[0][1] == () and len(calls[1][1]) == 1 and calls[1][1][0].goal == "open notepad"


async def test_replies_are_played_as_a_stream_when_streaming_is_available() -> None:
    hotkey = asyncio.Event()
    hotkey.set()
    stream = FakeSpeechStream([b"\x01\x00" * 100, b"\x02\x00" * 100])
    session, ui, speaker, _ = build(SPEECH, hotkey=hotkey, stream=stream)
    await session.run_once()
    assert getattr(speaker, "streamed", 0) == 1 and speaker.played == 0
    assert speaker.streamed_bytes == 400  # type: ignore[attr-defined]
    tts = ui.voice_calls[0][1]
    assert tts.kind == "tts" and "first audio in 0.4s" in tts.detail


async def test_a_stream_that_fails_before_any_audio_falls_back_to_the_whole_file() -> None:
    hotkey = asyncio.Event()
    hotkey.set()
    session, ui, speaker, _ = build(SPEECH, hotkey=hotkey, stream=LLMError("stream down"))
    result = await session.run_once()
    assert result is not None and speaker.played == 1  # spoken via the slower path
    assert any("Streaming speech failed" in e for e in ui.errors)
    assert ui.voice_calls[0][1].detail == "5 chars"


async def test_the_hotkey_cuts_a_streamed_reply_short() -> None:
    hotkey = asyncio.Event()
    hotkey.set()
    stream = FakeSpeechStream([b"\x01\x00" * 100] * 5)
    session, _, _, _ = build(
        SPEECH, hotkey=hotkey, stream=stream, speaker=FakeSpeaker(interrupt_playback=True)
    )
    await session.run_once()
    assert hotkey.is_set()  # the press is still pending so listening restarts immediately
