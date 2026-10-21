"""Speech output: turn an answer into plain spoken text, and play the model's audio on the
speakers while it is still arriving (stopped at once by the hotkey)."""

from __future__ import annotations

import asyncio
import contextlib
import re
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
_BEEPS = {"listen": (880.0, 0.12), "error": (330.0, 0.25)}  # (frequency Hz, seconds)
_BEEP_RATE = 24_000
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


def tone(freq: float, seconds: float, rate: int = _BEEP_RATE, volume: float = 0.2) -> Pcm:
    t = np.arange(int(rate * seconds)) / rate
    fade = np.minimum(1.0, np.minimum(t, seconds - t) / 0.01)  # avoid clicks
    return (np.sin(2 * np.pi * freq * t) * fade * volume * 32767).astype(np.int16)


class Speaker:
    """Plays audio on the default speakers."""

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

    async def beep(self, kind: Literal["listen", "error"]) -> None:
        freq, seconds = _BEEPS[kind]
        sd.play(tone(freq, seconds), _BEEP_RATE)  # pyright: ignore[reportUnknownMemberType]
        await asyncio.sleep(seconds + 0.05)
