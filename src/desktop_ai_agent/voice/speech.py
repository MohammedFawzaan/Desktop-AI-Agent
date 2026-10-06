"""Text helpers for voice: clean text for reading aloud, and strip the wake phrase."""

from __future__ import annotations

import re

_WAKE_PREFIX = re.compile(r"^\s*(hey|hi|okay|ok)[\s,.!]*friday[\s,.!?:;-]*", re.IGNORECASE)
_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_URL = re.compile(r"https?://\S+")
_MARKDOWN = re.compile(r"[*_`#>|~]+")
_BULLET = re.compile(r"^\s*(?:[-+]|\d+[.)])\s+", re.MULTILINE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def strip_wake_phrase(text: str) -> str:
    return _WAKE_PREFIX.sub("", text, count=1).strip()


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
