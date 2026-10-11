"""Text to speech output: make text speakable, decode the model's audio, and play it on the
speakers (streamed, interruptible)."""

from __future__ import annotations

import asyncio
import contextlib
import io
import re
import wave
from collections.abc import AsyncIterator
from typing import Any, Literal

import numpy as np
import sounddevice as sd  # pyright: ignore[reportMissingTypeStubs]

from friday.voice.microphone import Pcm

_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_URL = re.compile(r"https?://\S+")
_MARKDOWN = re.compile(r"[*_`#>|~]+")
_BULLET = re.compile(r"^\s*(?:[-+]|\d+[.)])\s+", re.MULTILINE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_BEEPS: dict[str, tuple[float, float]] = {
    "listen": (880.0, 0.12),  # I am listening
    "done": (660.0, 0.10),
    "error": (330.0, 0.25),
}
_PREBUFFER_S = 0.25  # audio to collect before starting, so playback never starves mid-word


def speakable(text: str, limit: int = 600) -> str:
    """Plain spoken-style text: no markdown or code, and short enough to listen to."""
    text = _CODE_BLOCK.sub(" ", text)
    text = _URL.sub("a link", text)
    text = _BULLET.sub("", text)
    text = _MARKDOWN.sub("", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [m.start() for m in _SENTENCE_END.finditer(cut)]
    cut = cut[: ends[-1] + 1] if ends else cut.rsplit(" ", 1)[0]
    return f"{cut} The full answer is on screen."


def wav_to_pcm(data: bytes, *, raw_rate: int = 24_000) -> tuple[Pcm, int]:
    """Decode a WAV file (any channel count, 16-bit) or headerless 16-bit mono PCM."""
    if data[:4] != b"RIFF":
        return np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16).copy(), raw_rate
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2:
            raise ValueError("Only 16-bit audio is supported.")
        channels = w.getnchannels()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        rate = w.getframerate()
    if channels > 1:
        samples = samples[: len(samples) // channels * channels].reshape(-1, channels)
        samples = samples.astype(np.int32).mean(axis=1).astype(np.int16)
    return samples.copy(), rate


def resample(pcm: Pcm, from_rate: int, to_rate: int) -> Pcm:
    if from_rate == to_rate or len(pcm) == 0:
        return pcm
    count = int(len(pcm) * to_rate / from_rate)
    positions = np.linspace(0, len(pcm) - 1, count)
    return np.interp(positions, np.arange(len(pcm)), pcm.astype(np.float32)).astype(np.int16)


def tone(freq: float, seconds: float, rate: int = 24_000, volume: float = 0.2) -> Pcm:
    t = np.arange(int(rate * seconds)) / rate
    fade = np.minimum(1.0, np.minimum(t, seconds - t) / 0.01)  # avoid clicks
    return (np.sin(2 * np.pi * freq * t) * fade * volume * 32767).astype(np.int16)


class Speaker:
    """Plays audio on the default speakers. Playback stops as soon as `interrupt` is set."""

    async def play(self, pcm: Pcm, rate: int, interrupt: asyncio.Event) -> bool:
        """True if it played to the end, False if it was interrupted."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + len(pcm) / rate + 0.3
        sd.play(pcm, rate)  # pyright: ignore[reportUnknownMemberType]
        try:
            while loop.time() < deadline:
                if interrupt.is_set():
                    return False
                await asyncio.sleep(0.05)
            return True
        finally:
            with contextlib.suppress(Exception):
                sd.stop()

    async def play_stream(
        self, chunks: AsyncIterator[bytes], rate: int, interrupt: asyncio.Event
    ) -> bool:
        """Play 16-bit mono PCM while it is still arriving. True if it finished, False if cut."""
        stream: Any = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16")
        stream.start()
        pending = bytearray()
        started = False
        prebuffer = int(rate * 2 * _PREBUFFER_S)

        async def write(block: bytes) -> None:
            await asyncio.to_thread(stream.write, block)

        try:
            async for data in chunks:
                if interrupt.is_set():
                    stream.abort()
                    return False
                pending += data
                if not started and len(pending) < prebuffer:
                    continue
                started = True
                whole = len(pending) // 2 * 2  # never split a 16-bit sample
                block = bytes(pending[:whole])
                del pending[:whole]
                if block:
                    await write(block)
            if len(pending) >= 2:
                await write(bytes(pending[: len(pending) // 2 * 2]))
            # stop() lets the audio already queued finish playing; abort() cuts it off.
            draining = asyncio.create_task(asyncio.to_thread(stream.stop))
            while not draining.done():
                if interrupt.is_set():
                    stream.abort()
                    await asyncio.gather(draining, return_exceptions=True)
                    return False
                await asyncio.sleep(0.05)
            await draining
            return True
        except Exception:
            if interrupt.is_set():  # an abort() made the blocked write fail; not a real error
                return False
            raise
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    async def beep(self, kind: Literal["listen", "done", "error"]) -> None:
        freq, seconds = _BEEPS[kind]
        await self.play(tone(freq, seconds), 24_000, asyncio.Event())
