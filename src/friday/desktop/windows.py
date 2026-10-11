"""Top-level windows through the Windows API: list, focus, show, close; plus DPI awareness."""

from __future__ import annotations

import contextlib
import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

from friday.desktop.input import tap_key

user32: Any = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
dwmapi: Any = ctypes.WinDLL("dwmapi")  # type: ignore[attr-defined]

HWND = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [HWND]
user32.IsWindow.argtypes = [HWND]
user32.IsIconic.argtypes = [HWND]
user32.IsZoomed.argtypes = [HWND]
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [HWND]
user32.BringWindowToTop.argtypes = [HWND]
user32.PostMessageW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetForegroundWindow.restype = HWND
user32.GetAncestor.argtypes = [HWND, wintypes.UINT]
user32.GetAncestor.restype = HWND
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_CLOAKED = 14
_GA_ROOT = 2
_SW = {"minimize": 6, "maximize": 3, "restore": 9}
_WM_CLOSE = 0x0010
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_HIDDEN_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
_HIDDEN_TITLES = {"Windows Input Experience", "Program Manager"}


def enable_dpi_awareness() -> None:
    """Use real pixels everywhere, so window, screenshot and mouse coordinates all agree."""
    with contextlib.suppress(AttributeError, OSError):
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor aware v2


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    title: str
    process: str
    pid: int
    class_name: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    focused: bool
    minimized: bool
    maximized: bool

    def label(self) -> str:
        state = " [focused]" if self.focused else " [minimized]" if self.minimized else ""
        return f'{self.hwnd} "{self.title}" ({self.process}){state}'


def _text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD(0)
    dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return bool(cloaked.value)


def _process(hwnd: int) -> tuple[int, str]:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = ""
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if handle:
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(handle)
    return pid.value, name


def foreground() -> int:
    hwnd = user32.GetForegroundWindow()
    return int(user32.GetAncestor(hwnd, _GA_ROOT) or hwnd or 0)


def window_info(hwnd: int, fg: int | None = None) -> WindowInfo:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    pid, process = _process(hwnd)
    return WindowInfo(
        hwnd=hwnd,
        title=_text(hwnd),
        process=process,
        pid=pid,
        class_name=_class_name(hwnd),
        rect=(rect.left, rect.top, rect.right, rect.bottom),
        focused=hwnd == (foreground() if fg is None else fg),
        minimized=bool(user32.IsIconic(hwnd)),
        maximized=bool(user32.IsZoomed(hwnd)),
    )


def list_windows() -> list[WindowInfo]:
    """Top-level windows a person would see in Alt+Tab."""
    found: list[int] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)

    def collect(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or not user32.GetWindowTextLengthW(hwnd):
            return True
        if user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW or _is_cloaked(hwnd):
            return True
        found.append(int(hwnd))
        return True

    user32.EnumWindows(proc_type(collect), 0)
    fg = foreground()
    windows = [window_info(h, fg) for h in found]
    return [
        w for w in windows if w.class_name not in _HIDDEN_CLASSES and w.title not in _HIDDEN_TITLES
    ]


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def focus(hwnd: int) -> bool:
    """Bring a window to the front. Windows limits this, so use the thread-attach technique."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW["restore"])
    current = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    attached = bool(
        target and target != current and user32.AttachThreadInput(current, target, True)
    )
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current, target, False)
    if foreground() != hwnd:
        # Last resort: Windows grants focus changes to the process that sent the latest input.
        tap_key(0x12)  # Alt
        user32.SetForegroundWindow(hwnd)
    deadline = time.monotonic() + 1.0
    while foreground() != hwnd and time.monotonic() < deadline:
        time.sleep(0.05)
    return foreground() == hwnd


def show(hwnd: int, action: str) -> None:
    user32.ShowWindow(hwnd, _SW[action])


def close(hwnd: int) -> None:
    user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
