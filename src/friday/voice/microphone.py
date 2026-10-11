"""Microphone input: 16 kHz mono frames from the Windows default (WASAPI) mic, device listing
and level checks."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

import numpy as np
import sounddevice as sd  # pyright: ignore[reportMissingTypeStubs]
from numpy.typing import NDArray

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 480  # 30 ms at 16 kHz

Pcm = NDArray[np.int16]


def rms(frame: Pcm) -> float:
    if len(frame) == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


@dataclass(frozen=True, slots=True)
class InputDevice:
    index: int
    host: str
    name: str
    channels: int
    is_default: bool


def _hostapis() -> list[dict[str, Any]]:
    return [dict(h) for h in sd.query_hostapis()]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType, reportUnknownMemberType]


def list_input_devices() -> list[InputDevice]:
    hostapis = _hostapis()
    devices: list[InputDevice] = []
    for index, raw in enumerate(sd.query_devices()):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType, reportUnknownMemberType]
        dev: dict[str, Any] = dict(raw)  # pyright: ignore[reportUnknownArgumentType]
        if dev["max_input_channels"] < 1:
            continue
        host: dict[str, Any] = hostapis[int(dev["hostapi"])]
        if host["name"] == "Windows WDM-KS":  # raw kernel streaming: not usable in shared mode
            continue
        devices.append(
            InputDevice(
                index=index,
                host=str(host["name"]).replace("Windows ", ""),
                name=str(dev["name"]),
                channels=int(dev["max_input_channels"]),
                is_default=host["default_input_device"] == index,
            )
        )
    return devices


def resolve_input_device(spec: int | str | None) -> tuple[int | None, Any]:
    """Pick the capture device. Default: the Windows default microphone through WASAPI.

    WASAPI follows the device chosen in Windows Sound settings and lets Windows convert the
    sample rate, so it is the most dependable path. `spec` may be a device index or a part of
    a device name.
    """
    if isinstance(spec, str) and spec.isdigit():
        spec = int(spec)
    devices = list_input_devices()
    wasapi = [d for d in devices if d.host == "WASAPI"]
    chosen: InputDevice | None = None
    if spec is None:
        chosen = next((d for d in wasapi if d.is_default), None) or next(
            (d for d in devices if d.is_default), None
        )
    elif isinstance(spec, int):
        chosen = next((d for d in devices if d.index == spec), None)
    else:
        needle = spec.lower()
        chosen = next((d for d in wasapi + devices if needle in d.name.lower()), None)
    if chosen is None:
        return None, None
    extra = sd.WasapiSettings(auto_convert=True) if chosen.host == "WASAPI" else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return chosen.index, extra  # pyright: ignore[reportUnknownVariableType]


def measure_level(spec: int | str | None, seconds: float = 3.0) -> tuple[float, int]:
    """Record for a few seconds; returns (average level, loudest sample)."""
    device, extra = resolve_input_device(spec)
    rec = sd.rec(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        int(SAMPLE_RATE * seconds),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        device=device,
        extra_settings=extra,
    )
    sd.wait()  # pyright: ignore[reportUnknownMemberType]
    samples: Pcm = np.asarray(rec, dtype=np.int16).reshape(-1)  # pyright: ignore[reportUnknownArgumentType]
    return rms(samples), int(np.abs(samples.astype(np.int32)).max())


class Microphone:
    """Continuously captures 16 kHz mono audio and hands out 30 ms frames."""

    def __init__(self, device: int | str | None = None) -> None:
        self._spec = device
        self._queue: asyncio.Queue[Pcm] = asyncio.Queue(maxsize=300)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: Any = None
        self._paused = False
        self.name = "default microphone"

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        device, extra = resolve_input_device(self._spec)
        if device is not None:
            self.name = str(sd.query_devices(device)["name"])  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=device,
            extra_settings=extra,
            callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata: Any, frames: int, time: Any, status: Any) -> None:
        # Runs on PortAudio's thread; hand the frame to the event loop.
        if self._paused or self._loop is None:
            return
        frame: Pcm = np.array(indata[:, 0], dtype=np.int16)
        with contextlib.suppress(RuntimeError):  # loop already closed
            self._loop.call_soon_threadsafe(self._push, frame)

    def _push(self, frame: Pcm) -> None:
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        self._queue.put_nowait(frame)

    async def read(self, wait_s: float) -> Pcm | None:
        try:
            async with asyncio.timeout(wait_s):
                return await self._queue.get()
        except TimeoutError:
            return None

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
        self._paused = False

    def close(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None
