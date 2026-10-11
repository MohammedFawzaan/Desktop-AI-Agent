"""Real mouse and keyboard input (SendInput), and parsing key descriptions like "ctrl+s"."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any

user32: Any = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]

MODIFIERS = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
NAMED = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "capslock": 0x14, "printscreen": 0x2C, "menu": 0x5D,
    "apps": 0x5D, "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "semicolon": 0xBA, "quote": 0xDE, "backslash": 0xDC,
    "volumeup": 0xAF, "volumedown": 0xAE, "mute": 0xAD, "playpause": 0xB3,
}  # fmt: skip
_PUNCTUATION = {"-": 0xBD, "=": 0xBB, ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE}
_NUMPAD = {
    "*": 0x6A,
    "multiply": 0x6A,
    "add": 0x6B,
    "subtract": 0x6D,
    "divide": 0x6F,
    "decimal": 0x6E,
}

Combo = tuple[tuple[int, ...], int]


class KeyParseError(ValueError):
    pass


def _vk(name: str) -> int:
    if name in NAMED:
        return NAMED[name]
    if name in _PUNCTUATION:
        return _PUNCTUATION[name]
    if name in _NUMPAD:
        return _NUMPAD[name]
    if name.startswith("num") and name[3:].isdigit() and len(name) == 4:
        return 0x60 + int(name[3])
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    raise KeyParseError(f"Unknown key '{name}'")


def parse_keys(spec: str) -> list[Combo]:
    """'ctrl+shift+s' -> one combo; 'ctrl+a, delete' -> two combos pressed in order."""
    combos: list[Combo] = []
    for chunk in spec.split(","):
        parts = [p.strip().lower() for p in chunk.split("+") if p.strip()]
        if not parts:
            continue
        *mods, key = parts
        if key in MODIFIERS and not mods:  # a lone modifier, e.g. "win"
            combos.append(((), MODIFIERS[key]))
            continue
        for mod in mods:
            if mod not in MODIFIERS:
                raise KeyParseError(f"'{mod}' is not a modifier (use ctrl, alt, shift or win)")
        combos.append((tuple(MODIFIERS[m] for m in mods), _vk(key)))
    if not combos:
        raise KeyParseError("No keys given")
    return combos


def normalize(spec: str) -> list[str]:
    """Canonical lowercase form of each combo, for safety checks: 'Alt + F4' -> ['alt+f4']."""
    return [
        "+".join(p.strip().lower() for p in chunk.split("+") if p.strip())
        for chunk in spec.split(",")
        if chunk.strip()
    ]


# ---- sending input -------------------------------------------------------------------------------

_INPUT_MOUSE, _INPUT_KEYBOARD = 0, 1
_KEYEVENTF_EXTENDEDKEY, _KEYEVENTF_KEYUP, _KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
_MOUSE_FLAGS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
_MOUSEEVENTF_WHEEL = 0x0800
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send(inputs: list[_INPUT]) -> None:
    if not inputs:
        return
    array = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT))


def _key(vk: int, up: bool) -> _INPUT:
    flags = (_KEYEVENTF_KEYUP if up else 0) | (_KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0)
    item = _INPUT(type=_INPUT_KEYBOARD)
    item.u.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    return item


def _unicode(code_unit: int, up: bool) -> _INPUT:
    item = _INPUT(type=_INPUT_KEYBOARD)
    flags = _KEYEVENTF_UNICODE | (_KEYEVENTF_KEYUP if up else 0)
    item.u.ki = _KEYBDINPUT(wVk=0, wScan=code_unit, dwFlags=flags, time=0, dwExtraInfo=0)
    return item


def tap_key(vk: int) -> None:
    _send([_key(vk, False), _key(vk, True)])


def press_combo(modifiers: list[int], vk: int) -> None:
    downs = [_key(m, False) for m in modifiers]
    ups = [_key(m, True) for m in reversed(modifiers)]
    _send([*downs, _key(vk, False), _key(vk, True), *ups])


def type_text(text: str, chunk: int = 64) -> None:
    """Type any text, including non-English characters, as real keystrokes."""
    events: list[_INPUT] = []
    for char in text.replace("\r\n", "\n"):
        if char == "\n":
            events += [_key(0x0D, False), _key(0x0D, True)]
        elif char == "\t":
            events += [_key(0x09, False), _key(0x09, True)]
        else:
            data = char.encode("utf-16-le")
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i : i + 2], "little")
                events += [_unicode(unit, False), _unicode(unit, True)]
    for start in range(0, len(events), chunk * 2):
        _send(events[start : start + chunk * 2])
        time.sleep(0.01)  # some apps drop keys if they arrive all at once


def click_at(x: int, y: int, button: str = "left", double: bool = False) -> None:
    user32.SetCursorPos(x, y)
    time.sleep(0.03)
    down, up = _MOUSE_FLAGS[button]
    for _ in range(2 if double else 1):
        events: list[_INPUT] = []
        for flag in (down, up):
            item = _INPUT(type=_INPUT_MOUSE)
            item.u.mi = _MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0)
            events.append(item)
        _send(events)
        time.sleep(0.06)


def scroll_at(x: int, y: int, clicks: int) -> None:
    """Positive clicks scroll up, negative scroll down."""
    user32.SetCursorPos(x, y)
    item = _INPUT(type=_INPUT_MOUSE)
    item.u.mi = _MOUSEINPUT(
        dx=0, dy=0, mouseData=ctypes.c_uint32(clicks * 120).value, dwFlags=_MOUSEEVENTF_WHEEL,
        time=0, dwExtraInfo=0,
    )  # fmt: skip
    _send([item])
