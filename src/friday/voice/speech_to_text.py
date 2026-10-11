"""Speech to text: detect when a spoken request starts and ends, and package it as WAV for the
speech model."""

from __future__ import annotations

import io
import wave
from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np

from friday.voice.microphone import FRAME_SAMPLES, SAMPLE_RATE, Pcm, rms

FRAME_S = FRAME_SAMPLES / SAMPLE_RATE


def pcm_to_wav(pcm: Pcm, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


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
