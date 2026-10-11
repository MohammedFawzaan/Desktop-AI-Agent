"""Local "Hey Friday" detection, so your room audio is never streamed to the cloud while idle."""

from __future__ import annotations

import json
import re
import shutil
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from friday.voice.microphone import Pcm

WAKE_PHRASE = "hey friday"
MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
_WAKE_PREFIX = re.compile(r"^\s*(hey|hi|okay|ok)[\s,.!]*friday[\s,.!?:;-]*", re.IGNORECASE)


def is_wake_text(text: str) -> bool:
    return WAKE_PHRASE in " ".join(text.lower().split())


def strip_wake_phrase(text: str) -> str:
    """Drop a leading "Hey Friday" from a transcript, leaving only the request."""
    return _WAKE_PREFIX.sub("", text, count=1).strip()


def ensure_model(data_dir: Path, say: Callable[[str], None] = lambda _m: None) -> Path:
    """Return the path of the small offline speech model, downloading it (~40 MB) once."""
    target = data_dir / "models" / MODEL_NAME
    if target.is_dir():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / f"{MODEL_NAME}.zip"
    say(f"Downloading the wake-word model once (~40 MB) from {MODEL_URL}")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as resp, archive.open("wb") as out:
            shutil.copyfileobj(resp, out)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target.parent)
    finally:
        archive.unlink(missing_ok=True)
    if not target.is_dir():
        raise RuntimeError(f"Wake-word model was not extracted to {target}")
    return target


class WakeWordDetector:
    """Listens to 30 ms frames and fires when it hears the wake phrase."""

    def __init__(self, model_path: Path) -> None:
        from vosk import KaldiRecognizer, Model, SetLogLevel  # pyright: ignore

        SetLogLevel(-1)
        self._rec: Any = KaldiRecognizer(
            Model(str(model_path)), 16_000, json.dumps([WAKE_PHRASE, "[unk]"])
        )

    def feed(self, frame: Pcm) -> bool:
        if self._rec.AcceptWaveform(frame.tobytes()):
            text = str(json.loads(self._rec.Result()).get("text", ""))
        else:
            text = str(json.loads(self._rec.PartialResult()).get("partial", ""))
        if is_wake_text(text):
            self.reset()
            return True
        return False

    def reset(self) -> None:
        self._rec.Reset()
