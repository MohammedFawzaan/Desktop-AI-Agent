from __future__ import annotations

import numpy as np

from friday.voice.microphone import FRAME_SAMPLES, SAMPLE_RATE, Pcm, rms
from friday.voice.speech_to_text import (
    NoiseTracker,
    SegmenterConfig,
    UtteranceSegmenter,
    pcm_to_wav,
)


def frame(amplitude: float) -> Pcm:
    t = np.arange(FRAME_SAMPLES) / SAMPLE_RATE
    return (np.sin(2 * np.pi * 440 * t) * amplitude).astype(np.int16)


QUIET = frame(20)
LOUD = frame(4000)


def run(segmenter: UtteranceSegmenter, frames: list[Pcm]):
    for f in frames:
        result = segmenter.feed(f)
        if result is not None:
            return result
    return None


def test_wav_has_a_header_and_the_samples() -> None:
    wav = pcm_to_wav(LOUD)
    assert wav[:4] == b"RIFF" and len(wav) == 44 + len(LOUD) * 2


def test_rms_scales_with_amplitude() -> None:
    assert rms(np.zeros(100, dtype=np.int16)) == 0.0
    assert rms(LOUD) > 100 * rms(QUIET) / 10


def test_noise_floor_is_a_low_percentile_not_the_speech_peak() -> None:
    tracker = NoiseTracker(window=100, percentile=20)
    for level in [30.0] * 80 + [3000.0] * 20:
        tracker.update(level)
    assert tracker.floor == 30.0


def test_segmenter_returns_the_spoken_part_after_enough_silence() -> None:
    seg = UtteranceSegmenter(noise_floor=20)
    result = run(seg, [QUIET] * 10 + [LOUD] * 20 + [QUIET] * 40)
    assert result is not None and result.reason == "speech" and result.pcm is not None
    seconds = len(result.pcm) / SAMPLE_RATE
    assert 0.6 < seconds < 2.5  # 0.6 s of speech plus pre-roll and trailing silence


def test_segmenter_times_out_when_nobody_speaks() -> None:
    result = run(UtteranceSegmenter(20, SegmenterConfig(start_timeout_s=1.0)), [QUIET] * 100)
    assert result is not None and result.reason == "timeout" and result.pcm is None


def test_a_single_click_is_not_speech() -> None:
    cfg = SegmenterConfig(start_timeout_s=1.0)
    result = run(UtteranceSegmenter(20, cfg), [QUIET, LOUD, QUIET] * 20)
    assert result is not None and result.reason == "timeout"


def test_very_short_noise_bursts_are_discarded() -> None:
    result = run(UtteranceSegmenter(20), [LOUD] * 3 + [QUIET] * 60)
    assert result is not None and result.reason == "too_short" and result.pcm is None


def test_a_noisy_room_raises_the_bar_for_speech() -> None:
    murmur = frame(1500)  # louder than min_rms but not 3x a noisy room's floor
    seg = UtteranceSegmenter(noise_floor=800)
    assert run(seg, [murmur] * 50) is None


def test_stop_ends_a_recording_early_and_max_length_caps_it() -> None:
    seg = UtteranceSegmenter(20)
    assert run(seg, [LOUD] * 20) is None
    stopped = seg.stop()
    assert stopped.reason == "stopped" and stopped.pcm is not None
    assert UtteranceSegmenter(20).stop().reason == "timeout"

    capped = run(UtteranceSegmenter(20, SegmenterConfig(max_s=1.0)), [LOUD] * 100)
    assert capped is not None and capped.reason == "max"
