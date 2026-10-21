"""The voice loop: wait for "Hey Friday" or the hotkey, listen, transcribe, act, speak the answer.

It talks to the mic, speaker and models only through the small interfaces below, so it can be
tested without real audio.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Literal, Protocol

from friday.agent.llm import CallInfo, LLMError
from friday.agent.models import RunResult, SessionTurn
from friday.voice.microphone import Pcm, rms
from friday.voice.speech_to_text import (
    NoiseTracker,
    SegmenterConfig,
    UtteranceSegmenter,
    pcm_to_wav,
)
from friday.voice.text_to_speech import speakable
from friday.voice.wake_word import strip_wake_phrase

_REMEMBERED_TURNS = 6


class Mic(Protocol):
    async def read(self, wait_s: float) -> Pcm | None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...


class WakeWord(Protocol):
    def feed(self, frame: Pcm) -> bool: ...
    def reset(self) -> None: ...


class SpeechStream(Protocol):
    sample_rate: int

    def chunks(self) -> AsyncIterator[bytes]: ...


class Speaker(Protocol):
    async def play_stream(
        self, chunks: AsyncIterator[bytes], rate: int, interrupt: asyncio.Event
    ) -> bool: ...
    async def beep(self, kind: Literal["listen", "error"]) -> None: ...


class VoiceUI(Protocol):
    def status(self, message: str) -> None: ...
    def heard(self, text: str) -> None: ...
    def answered(self, result: RunResult) -> None: ...
    def error(self, message: str) -> None: ...


Transcribe = Callable[[bytes], Awaitable[tuple[str, CallInfo]]]
Speak = Callable[[str], Awaitable[SpeechStream]]
RunAgent = Callable[[str, Sequence[SessionTurn]], Awaitable[RunResult]]


class VoiceSession:
    def __init__(
        self,
        *,
        mic: Mic,
        wake: WakeWord | None,
        hotkey: asyncio.Event,
        speaker: Speaker,
        transcribe: Transcribe,
        speak: Speak,
        run_agent: RunAgent,
        ui: VoiceUI,
        segmenter: SegmenterConfig | None = None,
    ) -> None:
        self._mic = mic
        self._wake = wake
        self._hotkey = hotkey  # pressed: start talking, stop recording early, or cut Friday off
        self._speaker = speaker
        self._transcribe = transcribe
        self._speak = speak
        self._run_agent = run_agent
        self._ui = ui
        self._segmenter = segmenter or SegmenterConfig()
        self._noise = NoiseTracker()
        self._history: list[SessionTurn] = []

    async def run(self) -> None:
        while True:
            self._ui.status("Waiting for 'Hey Friday' or the hotkey...")
            await self.run_once()

    async def run_once(self) -> RunResult | None:
        """One exchange. Errors are reported and the loop goes on."""
        await self._wait_for_trigger()
        try:
            return await self._converse()
        except Exception as exc:
            self._ui.error(f"{type(exc).__name__}: {exc}")
            return None
        finally:
            if self._wake is not None:
                self._wake.reset()

    async def _wait_for_trigger(self) -> None:
        while not self._hotkey.is_set():
            frame = await self._mic.read(0.1)
            if frame is None:
                continue
            self._noise.update(rms(frame))  # learn how loud the quiet room is
            if self._wake is not None and self._wake.feed(frame):
                return
        self._hotkey.clear()

    async def _converse(self) -> RunResult | None:
        self._ui.status("Listening...")
        self._mic.pause()  # so the beep is not recorded
        await self._speaker.beep("listen")
        self._mic.resume()

        pcm = await self._record()
        if pcm is None:
            self._ui.status("I didn't hear anything.")
            return None
        try:
            text, _ = await self._transcribe(pcm_to_wav(pcm))
        except LLMError as exc:
            self._ui.error(f"Speech recognition failed: {exc}")
            await self._speaker.beep("error")
            return None
        text = strip_wake_phrase(text)
        if not text:
            self._ui.status("I didn't catch that.")
            return None

        self._ui.heard(text)
        result = await self._run_agent(text, tuple(self._history))
        self._history.append(SessionTurn(text, result.status, result.summary[:300]))
        del self._history[:-_REMEMBERED_TURNS]
        self._ui.answered(result)
        await self._say(result.summary)
        return result

    async def _record(self) -> Pcm | None:
        """Record until the speaker stops talking (or presses the hotkey again)."""
        segmenter = UtteranceSegmenter(self._noise.floor, self._segmenter)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._segmenter.start_timeout_s + self._segmenter.max_s + 5
        while loop.time() < deadline:
            if self._hotkey.is_set():
                self._hotkey.clear()
                return segmenter.stop().pcm
            frame = await self._mic.read(0.5)
            segment = segmenter.feed(frame) if frame is not None else None
            if segment is not None:
                return segment.pcm
        return None

    async def _say(self, text: str) -> None:
        spoken = speakable(text)
        if not spoken:
            return
        try:
            stream = await self._speak(spoken)  # returns once the first audio has arrived
        except LLMError as exc:
            self._ui.error(f"Text-to-speech failed: {exc}")
            return
        # A hotkey press cuts the reply short and stays pending, so listening starts right away.
        self._mic.pause()  # so Friday does not hear herself
        try:
            await self._speaker.play_stream(stream.chunks(), stream.sample_rate, self._hotkey)
        finally:
            self._mic.resume()
