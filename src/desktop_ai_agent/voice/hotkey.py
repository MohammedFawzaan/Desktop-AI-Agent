"""A system-wide hotkey (works while any app has focus), via the Windows RegisterHotKey API."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from ctypes import wintypes

_MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
_NAMED_KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012


class HotkeyError(Exception):
    pass


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+f' -> (modifier flags, virtual-key code). At least one modifier is required."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        raise HotkeyError(f"Hotkey '{spec}' needs a modifier and a key, e.g. ctrl+alt+f")
    *mods, key = parts
    flags = 0
    for mod in mods:
        if mod not in _MODIFIERS:
            raise HotkeyError(f"Unknown modifier '{mod}' in hotkey '{spec}'")
        flags |= _MODIFIERS[mod]
    if key in _NAMED_KEYS:
        return flags, _NAMED_KEYS[key]
    if len(key) == 1 and key.isalnum():
        return flags, ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return flags, 0x70 + int(key[1:]) - 1
    raise HotkeyError(f"Unknown key '{key}' in hotkey '{spec}'")


class HotkeyListener:
    """Calls `callback` (from a background thread) every time the hotkey is pressed."""

    def __init__(self, spec: str, callback: Callable[[], None]) -> None:
        self._flags, self._vk = parse_hotkey(spec)
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="friday-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)
        if self._error:
            raise HotkeyError(self._error)

    def _run(self) -> None:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()  # type: ignore[attr-defined]
        if not user32.RegisterHotKey(None, 1, self._flags | _MOD_NOREPEAT, self._vk):
            self._error = "Could not register the hotkey; another app may already use it."
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY:
                    self._callback()
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)  # type: ignore[attr-defined]
            self._thread.join(timeout=1)
        self._thread = None
