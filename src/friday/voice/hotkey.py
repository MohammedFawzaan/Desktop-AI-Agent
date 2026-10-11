"""A system-wide hotkey (works while any app has focus), via the Windows RegisterHotKey API."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from ctypes import wintypes

from friday.desktop.input import KeyParseError, parse_keys

# Alt, Ctrl, Shift, Win virtual keys -> RegisterHotKey modifier flags.
_MOD_FLAGS = {0x12: 0x1, 0x11: 0x2, 0x10: 0x4, 0x5B: 0x8}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012


class HotkeyError(Exception):
    pass


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+f' -> (modifier flags, virtual-key code). At least one modifier is required."""
    try:
        combos = parse_keys(spec)
    except KeyParseError as exc:
        raise HotkeyError(f"Bad hotkey '{spec}': {exc}") from exc
    if len(combos) != 1 or not combos[0][0]:
        raise HotkeyError(f"Hotkey '{spec}' needs a modifier and a key, e.g. ctrl+alt+f")
    modifiers, vk = combos[0]
    flags = 0
    for mod in modifiers:
        flags |= _MOD_FLAGS[mod]
    return flags, vk


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
