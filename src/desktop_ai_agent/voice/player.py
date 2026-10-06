from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any, Literal

import sounddevice as sd  # pyright: ignore[reportMissingTypeStubs]

from desktop_ai_agent.voice.audio import Pcm, tone

_BEEPS: dict[str, tuple[float, float]] = {
    "listen": (880.0, 0.12),  # I am listening
    "done": (660.0, 0.10),
    "error": (330.0, 0.25),
}
_PREBUFFER_S = 0.25  # audio to collect before starting, so playback never starves mid-word


class Player:
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
