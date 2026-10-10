# mss ships without type information; this module is the boundary that hides it.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Screenshots for the vision fallback, and mapping the model's boxes back to screen pixels."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import mss
from PIL import Image

_MAX_SIDE = 1600  # enough detail to read UI text, small enough to keep tokens down


@dataclass(frozen=True, slots=True)
class Shot:
    png: bytes
    left: int
    top: int
    width: int
    height: int


def capture(rect: tuple[int, int, int, int] | None = None) -> Shot:
    """Grab a screen region (left, top, right, bottom) or, without one, every monitor."""
    grabber: Any
    with mss.MSS() as grabber:
        if rect is None:
            mon: dict[str, int] = grabber.monitors[0]
            left, top, width, height = mon["left"], mon["top"], mon["width"], mon["height"]
        else:
            left, top = rect[0], rect[1]
            width, height = max(1, rect[2] - rect[0]), max(1, rect[3] - rect[1])
        raw: Any = grabber.grab({"left": left, "top": top, "width": width, "height": height})
    image = Image.frombytes("RGB", tuple(raw.size), bytes(raw.bgra), "raw", "BGRX")
    scale = min(1.0, _MAX_SIDE / max(image.size))
    if scale < 1.0:
        image = image.resize((round(image.width * scale), round(image.height * scale)))
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return Shot(buf.getvalue(), left, top, width, height)


def box_to_point(box: Sequence[int], shot: Shot) -> tuple[int, int]:
    """Gemini boxes are [ymin, xmin, ymax, xmax] on a 0-1000 grid; return the screen centre."""
    ymin, xmin, ymax, xmax = (min(1000, max(0, v)) for v in box[:4])
    x = shot.left + round((xmin + xmax) / 2 / 1000 * shot.width)
    y = shot.top + round((ymin + ymax) / 2 / 1000 * shot.height)
    return x, y
