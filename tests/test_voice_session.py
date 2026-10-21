from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Sequence
from typing import Literal

import numpy as np

from friday.agent.llm import CallInfo, LLMError, Usage
from friday.agent.models import RunResult, SessionTurn
from friday.voice.microphone import FRAME_SAMPLES, SAMPLE_RATE, Pcm
from friday.voice.session import VoiceSession

INFO = CallInfo(Usage(10, 5, 0), "fake-audio-model", 0.5)


def frame(amplitude: float) -> Pcm:
    t = np.arange(FRAME_SAMPLES) / SAMPLE_RATE
    return (np.sin(2 * np.pi * 440 * t) * amplitude).astype(np.int16)


QUIET, LOUD = frame(20), frame(4000)
SPEECH = [LOUD] * 20 + [QUIET] * 40


class FakeMic:
    def __init__(self, frames: list[Pcm]) -> None:
        self.frames = deque(frames)

    async def read(self, wait_s: float) -> Pcm | None:
        await asyncio.sleep(0)
        return self.frames.popleft() if self.frames else None

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


class FakeWake:
    def __init__(self, fire_on_frame: int) -> None:
        self.fed = 0
        self.fire_on = fire_on_frame

    def feed(self, frame: Pcm) -> bool:
        self.fed += 1
        return self.fed == self.fire_on

    def reset(self) -> None:
        pass


class FakeSpeaker:
    def __init__(self, interrupt: bool = False) -> None:
        self.beeps: list[str] = []
        self.played_bytes = 0
        self.interrupt = interrupt  # the user presses the hotkey while Friday is talking

    async def play_stream(
        self, chunks: AsyncIterator[bytes], rate: int, interrupt: asyncio.Event
    ) -> bool:
        async for data in chunks:
            self.played_bytes += len(data)
            if self.interrupt:
                interrupt.set()
                return False
        return True

    async def beep(self, kind: Literal["listen", "error"]) -> None:
        self.beeps.append(kind)


class FakeSpeechStream:
    sample_rate = 24_000

    async def chunks(self) -> AsyncIterator[bytes]:
        for _ in range(2):
            yield b"\x01\x00" * 100


class RecorderUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.heard_text: list[str] = []
        self.errors: list[str] = []
        self.answers: list[RunResult] = []

    def status(self, message: str) -> None:
        self.messages.append(message)

    def heard(self, text: str) -> None:
        self.heard_text.append(text)

    def answered(self, result: RunResult) -> None:
        self.answers.append(result)

    def error(self, message: str) -> None:
        self.errors.append(message)


def build(
    frames: list[Pcm],
    *,
    transcript: str | Exception = "hey friday open notepad",
    wake: FakeWake | None = None,
    pressed: bool = True,
    speaker: FakeSpeaker | None = None,
    agent_error: Exception | None = None,
    speech_error: Exception | None = None,
) -> tuple[VoiceSession, RecorderUI, FakeSpeaker, list[tuple[str, Sequence[SessionTurn]]]]:
    ui, speaker = RecorderUI(), speaker or FakeSpeaker()
    hotkey = asyncio.Event()
    if pressed:
        hotkey.set()
    agent_calls: list[tuple[str, Sequence[SessionTurn]]] = []

    async def transcribe(wav: bytes) -> tuple[str, CallInfo]:
        if isinstance(transcript, Exception):
            raise transcript
        return transcript, INFO

    async def speak(text: str) -> FakeSpeechStream:
        if speech_error is not None:
            raise speech_error
        return FakeSpeechStream()

    async def run_agent(text: str, history: Sequence[SessionTurn]) -> RunResult:
        agent_calls.append((text, history))
        if agent_error:
            raise agent_error
        return RunResult("success", "Done.", "r1", text, 1, Usage(), 0.1)

    session = VoiceSession(
        mic=FakeMic(frames),
        wake=wake,
        hotkey=hotkey,
        speaker=speaker,
        transcribe=transcribe,
        speak=speak,
        run_agent=run_agent,
        ui=ui,
    )
    return session, ui, speaker, agent_calls


async def test_wake_word_to_spoken_answer() -> None:
    session, ui, speaker, calls = build(
        [QUIET] * 3 + SPEECH, wake=FakeWake(fire_on_frame=3), pressed=False
    )
    result = await session.run_once()
    assert result is not None and result.summary == "Done."
    assert calls == [("open notepad", ())]  # the wake phrase was removed
    assert ui.heard_text == ["open notepad"] and speaker.beeps == ["listen"]
    assert speaker.played_bytes == 400  # the streamed reply was played


async def test_the_hotkey_starts_a_conversation_without_a_wake_word() -> None:
    session, _, _, calls = build(SPEECH, wake=None)
    assert await session.run_once() is not None and len(calls) == 1


async def test_silence_after_the_trigger_does_nothing() -> None:
    session, ui, _, calls = build([QUIET] * 300)
    assert await session.run_once() is None
    assert calls == [] and "I didn't hear anything." in ui.messages


async def test_an_empty_transcript_does_not_run_the_agent() -> None:
    session, ui, _, calls = build(SPEECH, transcript="hey friday")
    assert await session.run_once() is None
    assert calls == [] and "I didn't catch that." in ui.messages


async def test_failures_are_reported_and_the_loop_survives() -> None:
    session, ui, speaker, _ = build(SPEECH, transcript=LLMError("down"))
    assert await session.run_once() is None
    assert "Speech recognition failed" in ui.errors[0] and speaker.beeps == ["listen", "error"]

    session, ui, _, _ = build(SPEECH, agent_error=RuntimeError("boom"))
    assert await session.run_once() is None and "RuntimeError: boom" in ui.errors[0]

    session, ui, _, _ = build(SPEECH, speech_error=LLMError("tts down"))
    assert await session.run_once() is not None and "Text-to-speech failed" in ui.errors[0]


async def test_the_hotkey_cuts_a_reply_short_and_listening_starts_again() -> None:
    session, _, speaker, _ = build(SPEECH, speaker=FakeSpeaker(interrupt=True))
    await session.run_once()
    assert speaker.played_bytes == 200  # stopped after the first chunk
    assert session._hotkey.is_set()  # pyright: ignore[reportPrivateUsage]


async def test_follow_up_requests_remember_the_previous_one() -> None:
    session, _, _, calls = build(SPEECH + SPEECH)
    await session.run_once()
    session._hotkey.set()  # pyright: ignore[reportPrivateUsage]
    await session.run_once()
    assert calls[0][1] == () and calls[1][1][0].goal == "open notepad"
