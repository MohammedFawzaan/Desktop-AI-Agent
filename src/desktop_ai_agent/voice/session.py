"""The voice loop: wait for "Hey Friday" or the hotkey, listen, transcribe, act, speak."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from desktop_ai_agent.agent.state import RunResult, SessionTurn
from desktop_ai_agent.llm.base import CallInfo, LLMError
from desktop_ai_agent.voice.audio import (
    SAMPLE_RATE,
    NoiseTracker,
    Pcm,
    SegmenterConfig,
    UtteranceSegmenter,
    pcm_to_wav,
    rms,
    wav_to_pcm,
)
from desktop_ai_agent.voice.speech import speakable, strip_wake_phrase

Trigger = Literal["wake", "hotkey"]


@dataclass(frozen=True, slots=True)
class VoiceCall:
    kind: Literal["stt", "tts"]
    info: CallInfo
    detail: str


class MicLike(Protocol):
    async def read(self, wait_s: float) -> Pcm | None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...


class WakeLike(Protocol):
    def feed(self, frame: Pcm) -> bool: ...
    def reset(self) -> None: ...


class SpeechStream(Protocol):
    sample_rate: int
    first_audio_s: float

    @property
    def info(self) -> CallInfo: ...

    def chunks(self) -> AsyncIterator[bytes]: ...


class PlayerLike(Protocol):
    async def play(self, pcm: Pcm, rate: int, interrupt: asyncio.Event) -> bool: ...
    async def play_stream(
        self, chunks: AsyncIterator[bytes], rate: int, interrupt: asyncio.Event
    ) -> bool: ...
    async def beep(self, kind: Literal["listen", "done", "error"]) -> None: ...


class VoiceUI(Protocol):
    def status(self, message: str) -> None: ...
    def heard(self, text: str) -> None: ...
    def error(self, message: str) -> None: ...
    def result(self, result: RunResult, calls: list[VoiceCall]) -> None: ...
    def voice_metrics(self, calls: list[VoiceCall]) -> None: ...


Transcribe = Callable[[bytes], Awaitable[tuple[str, CallInfo]]]
Synthesize = Callable[[str], Awaitable[tuple[bytes, CallInfo]]]
SynthesizeStream = Callable[[str], Awaitable[SpeechStream]]
RunAgent = Callable[[str, Sequence[SessionTurn]], Awaitable[RunResult]]


class VoiceSession:
    def __init__(
        self,
        *,
        mic: MicLike,
        wake: WakeLike | None,
        hotkey: asyncio.Event,
        player: PlayerLike,
        transcribe: Transcribe,
        synthesize: Synthesize,
        run_agent: RunAgent,
        ui: VoiceUI,
        synthesize_stream: SynthesizeStream | None = None,
        segmenter: SegmenterConfig | None = None,
        max_history: int = 6,
    ) -> None:
        self._mic = mic
        self._wake = wake
        self._hotkey = hotkey
        self._player = player
        self._transcribe = transcribe
        self._synthesize = synthesize
        self._synthesize_stream = synthesize_stream
        self._run_agent = run_agent
        self._ui = ui
        self._cfg = segmenter or SegmenterConfig()
        self._max_history = max_history
        self._noise = NoiseTracker()
        self._history: list[SessionTurn] = []

    async def run(self) -> None:
        while True:
            self._ui.status("Waiting for 'Hey Friday' or the hotkey...")
            await self.run_once()

    async def run_once(self) -> RunResult | None:
        trigger = await self._wait_for_trigger()
        try:
            return await self._handle(trigger)
        except Exception as exc:  # keep the voice loop alive whatever goes wrong in one turn
            self._ui.error(f"{type(exc).__name__}: {exc}")
            return None

    async def _wait_for_trigger(self) -> Trigger:
        while True:
            if self._hotkey.is_set():
                self._hotkey.clear()
                return "hotkey"
            frame = await self._mic.read(0.1)
            if frame is None:
                continue
            self._noise.update(rms(frame))
            if self._wake is not None and self._wake.feed(frame):
                return "wake"

    async def _handle(self, trigger: Trigger) -> RunResult | None:
        self._ui.status("Listening...")
        self._mic.pause()
        await self._player.beep("listen")
        self._mic.resume()

        pcm = await self._record()
        if pcm is None:
            self._ui.status("I didn't hear anything.")
            self._reset_wake()
            return None

        try:
            text, stt_info = await self._transcribe(pcm_to_wav(pcm))
        except LLMError as exc:
            self._ui.error(f"Speech recognition failed: {exc}")
            await self._player.beep("error")
            self._reset_wake()
            return None
        calls = [VoiceCall("stt", stt_info, f"{len(pcm) / SAMPLE_RATE:.1f}s of audio")]
        text = strip_wake_phrase(text)
        if not text:
            self._ui.status("I didn't catch that.")
            self._reset_wake()
            return None

        self._ui.heard(text)
        result = await self._run_agent(text, tuple(self._history))
        self._history.append(SessionTurn(text, result.status, result.summary[:300]))
        del self._history[: -self._max_history]
        self._ui.result(result, calls)

        await self._speak(result.summary, calls)
        self._reset_wake()
        return result

    async def _record(self) -> Pcm | None:
        segmenter = UtteranceSegmenter(self._noise.floor, self._cfg)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cfg.start_timeout_s + self._cfg.max_s + 5
        while loop.time() < deadline:
            if self._hotkey.is_set():  # pressing the hotkey again ends the recording
                self._hotkey.clear()
                return segmenter.stop().pcm
            frame = await self._mic.read(0.5)
            if frame is None:
                continue
            segment = segmenter.feed(frame)
            if segment is not None:
                return segment.pcm
        return None

    async def _speak(self, text: str, calls: list[VoiceCall]) -> None:
        spoken = speakable(text)
        if not spoken:
            return
        if self._synthesize_stream is not None:
            try:
                await self._speak_streamed(spoken, calls)
                return
            except LLMError as exc:  # failed before any audio: use the slower whole-file method
                self._ui.error(f"Streaming speech failed ({exc}); trying the slower method.")
        await self._speak_whole(spoken, calls)

    async def _speak_streamed(self, spoken: str, calls: list[VoiceCall]) -> None:
        assert self._synthesize_stream is not None
        stream = await self._synthesize_stream(spoken)  # returns once the first audio arrived
        self._mic.pause()
        try:
            await self._player.play_stream(stream.chunks(), stream.sample_rate, self._hotkey)
        finally:
            self._mic.resume()
        detail = f"{len(spoken)} chars, first audio in {stream.first_audio_s:.1f}s"
        calls.append(VoiceCall("tts", stream.info, detail))
        self._ui.voice_metrics(calls)

    async def _speak_whole(self, spoken: str, calls: list[VoiceCall]) -> None:
        try:
            wav, tts_info = await self._synthesize(spoken)
            pcm, rate = wav_to_pcm(wav)
        except (LLMError, ValueError) as exc:
            self._ui.error(f"Text-to-speech failed: {exc}")
            return
        calls.append(VoiceCall("tts", tts_info, f"{len(spoken)} chars"))
        self._mic.pause()
        try:
            await self._player.play(pcm, rate, self._hotkey)  # a hotkey press cuts it short
        finally:
            self._mic.resume()
        self._ui.voice_metrics(calls)

    def _reset_wake(self) -> None:
        if self._wake is not None:
            self._wake.reset()
