"""Audio primitives: WAV conversion, resampling, tones, and speech start/end detection."""

from __future__ import annotations

import io
import wave
from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 480  # 30 ms at 16 kHz
FRAME_S = FRAME_SAMPLES / SAMPLE_RATE

Pcm = NDArray[np.int16]


def pcm_to_wav(pcm: Pcm, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


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


def rms(frame: Pcm) -> float:
    if len(frame) == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def tone(freq: float, seconds: float, rate: int = 24_000, volume: float = 0.2) -> Pcm:
    t = np.arange(int(rate * seconds)) / rate
    fade = np.minimum(1.0, np.minimum(t, seconds - t) / 0.01)  # avoid clicks
    return (np.sin(2 * np.pi * freq * t) * fade * volume * 32767).astype(np.int16)


class NoiseTracker:
    """Estimates the room's background level: a low percentile of recent frame energies."""

    def __init__(self, window: int = 100, percentile: float = 20.0) -> None:
        self._levels: deque[float] = deque(maxlen=window)
        self._percentile = percentile

    def update(self, level: float) -> None:
        self._levels.append(level)

    @property
    def floor(self) -> float:
        if not self._levels:
            return 0.0
        return float(np.percentile(list(self._levels), self._percentile))


@dataclass(frozen=True, slots=True)
class SegmenterConfig:
    start_timeout_s: float = 6.0  # give up if nobody starts speaking
    end_silence_s: float = 1.0  # this much silence ends the utterance
    max_s: float = 30.0
    preroll_s: float = 0.3
    min_speech_s: float = 0.25
    min_rms: float = 250.0
    speech_factor: float = 3.0  # speech must be this many times louder than the room


@dataclass(frozen=True, slots=True)
class Segment:
    pcm: Pcm | None
    reason: Literal["speech", "timeout", "too_short", "max", "stopped"]


class UtteranceSegmenter:
    """Feed 30 ms frames; it reports when a spoken utterance has started and ended."""

    def __init__(self, noise_floor: float, config: SegmenterConfig | None = None) -> None:
        self._cfg = config or SegmenterConfig()
        self._start = max(self._cfg.min_rms, noise_floor * self._cfg.speech_factor)
        self._keep = self._start * 0.6
        self._preroll: deque[Pcm] = deque(maxlen=max(1, round(self._cfg.preroll_s / FRAME_S)))
        self._frames: list[Pcm] = []
        self._speaking = False
        self._loud_run = 0
        self._elapsed = 0.0
        self._silence = 0.0
        self._speech_s = 0.0

    def feed(self, frame: Pcm) -> Segment | None:
        self._elapsed += FRAME_S
        level = rms(frame)
        if not self._speaking:
            self._loud_run = self._loud_run + 1 if level > self._start else 0
            self._preroll.append(frame)
            if self._loud_run >= 2:  # two loud frames in a row: ignore single clicks
                self._speaking = True
                self._frames = list(self._preroll)
                self._speech_s = self._loud_run * FRAME_S
                self._silence = 0.0
                self._elapsed = 0.0
            elif self._elapsed >= self._cfg.start_timeout_s:
                return Segment(None, "timeout")
            return None

        self._frames.append(frame)
        if level > self._keep:
            self._silence = 0.0
            self._speech_s += FRAME_S
        else:
            self._silence += FRAME_S
        if self._silence >= self._cfg.end_silence_s:
            return self._finish("speech")
        if self._elapsed >= self._cfg.max_s:
            return self._finish("max")
        return None

    def stop(self) -> Segment:
        """The user ended the recording early (second hotkey press)."""
        if not self._speaking:
            return Segment(None, "timeout")
        return self._finish("stopped")

    def _finish(self, reason: Literal["speech", "max", "stopped"]) -> Segment:
        if self._speech_s < self._cfg.min_speech_s:
            return Segment(None, "too_short")
        return Segment(np.concatenate(self._frames), reason)
