"""GUI control: see windows and their controls, and click, type and press keys in them.

All Windows UI Automation (COM) work happens on one dedicated worker thread, because COM objects
must stay on the thread that created them.
"""

from __future__ import annotations

import asyncio
import functools
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse

from desktop_ai_agent.gui import win32
from desktop_ai_agent.gui.apps import StartApp, launch, load_start_apps, match_app, suggestions
from desktop_ai_agent.gui.elements import ElementInfo, describe, select
from desktop_ai_agent.gui.keys import parse_keys
from desktop_ai_agent.gui.screen import Shot, capture
from desktop_ai_agent.gui.uia import Uia
from desktop_ai_agent.gui.win32 import WindowInfo

R = TypeVar("R")
_CTRL, _END, _ENTER = 0x11, 0x23, 0x0D
_SETTLE_S = 0.3


class GuiError(Exception):
    pass


@dataclass(slots=True)
class _Ref:
    element: Any
    info: ElementInfo
    hwnd: int


class GuiController:
    def __init__(self) -> None:
        win32.enable_dpi_awareness()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="friday-gui")
        self._uia: Uia | None = None
        self._refs: dict[int, _Ref] = {}
        self._by_window: dict[int, list[int]] = {}
        self._next_id = 1
        self._apps: list[StartApp] | None = None
        self._last_window: int | None = None

    async def _run(self, fn: Callable[..., R], *args: Any) -> R:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, functools.partial(fn, *args))

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def describe_element(self, element_id: int) -> str | None:
        """Name of a previously read element (used by the safety policy; no COM calls)."""
        ref = self._refs.get(element_id)
        return ref.info.name if ref else None

    # ---- public async API ------------------------------------------------------------------

    async def list_windows(self) -> str:
        windows = await self._run(win32.list_windows)
        if not windows:
            return "No open windows."
        return "\n".join(w.label() for w in windows)

    async def read_window(self, window: str | None, find: str = "", limit: int = 120) -> str:
        return await self._run(self._read, window, find, limit)

    async def click(
        self, element: int | None, x: int | None, y: int | None, button: str, double: bool
    ) -> str:
        return await self._run(self._click, element, x, y, button, double)

    async def type_text(
        self, text: str, element: int | None, window: str | None, replace: bool, enter: bool
    ) -> str:
        return await self._run(self._type, text, element, window, replace, enter)

    async def press_keys(self, keys: str, window: str | None) -> str:
        return await self._run(self._press, keys, window)

    async def scroll(
        self, direction: str, amount: int, element: int | None, window: str | None
    ) -> str:
        return await self._run(self._scroll, direction, amount, element, window)

    async def focus_window(self, window: str) -> str:
        return await self._run(self._focus, window)

    async def window_action(self, window: str, action: str) -> str:
        return await self._run(self._window_action, window, action)

    async def open_app(self, name: str) -> str:
        return await self._run(self._open_app, name)

    async def open_url(self, url: str) -> str:
        return await self._run(self._open_url, url)

    async def screenshot(self, window: str | None) -> Shot:
        return await self._run(self._screenshot, window)

    # ---- worker-thread implementations ------------------------------------------------------

    def _u(self) -> Uia:
        if self._uia is None:
            self._uia = Uia()
        return self._uia

    def _resolve(self, spec: str | None) -> WindowInfo:
        if spec is None or not spec.strip():
            if self._last_window and win32.is_window(self._last_window):
                return win32.window_info(self._last_window)
            raise GuiError("Say which window (title, app name or id). Use list_windows.")
        text = spec.strip()
        if text.isdigit():
            if win32.is_window(int(text)):
                return win32.window_info(int(text))
            raise GuiError(f"No window with id {text}. Use list_windows.")
        windows = win32.list_windows()
        needle = text.lower().removesuffix(".exe")
        matches = [
            w
            for w in windows
            if needle in w.title.lower() or needle in w.process.lower().removesuffix(".exe")
        ]
        if not matches:
            names = "; ".join(w.label() for w in windows[:12]) or "none"
            raise GuiError(f"No open window matches '{spec}'. Open windows: {names}")
        matches.sort(key=lambda w: (not w.focused, w.minimized))
        return matches[0]

    def _ref(self, element_id: int) -> _Ref:
        ref = self._refs.get(element_id)
        if ref is None:
            raise GuiError(f"Unknown element [{element_id}]. Read the window to get current ids.")
        if not self._u().alive(ref.element):
            raise GuiError(f"Element [{element_id}] no longer exists. Read the window again.")
        return ref

    def _report(self, before: set[int]) -> str:
        """What changed on screen after an action, so the agent can see the effect."""
        time.sleep(_SETTLE_S)
        windows = win32.list_windows()
        focused = next((w for w in windows if w.focused), None)
        parts = [f"Focused window: {focused.label()}." if focused else "No focused window."]
        new = [w.label() for w in windows if w.hwnd not in before]
        if new:
            parts.append("New window(s): " + "; ".join(new) + ".")
        return " ".join(parts)

    def _hwnds(self) -> set[int]:
        return {w.hwnd for w in win32.list_windows()}

    def _read(self, spec: str | None, find: str, limit: int) -> str:
        win = self._resolve(spec)
        if win.minimized:
            win32.show(win.hwnd, "restore")
            time.sleep(0.4)
            win = win32.window_info(win.hwnd)
        self._last_window = win.hwnd
        items = self._u().snapshot(win.hwnd)
        for old in self._by_window.pop(win.hwnd, []):
            self._refs.pop(old, None)
        chosen, matched = select([info for _, info in items], find, limit)
        lines: list[str] = []
        ids: list[int] = []
        for index in chosen:
            element, info = items[index]
            element_id = self._next_id
            self._next_id += 1
            self._refs[element_id] = _Ref(element, info, win.hwnd)
            ids.append(element_id)
            lines.append(f"[{element_id}] {describe(info)}")
        self._by_window[win.hwnd] = ids
        header = f"Window {win.label()}"
        if not lines:
            hint = f" matching '{find}'" if find else ""
            return (
                f"{header}\nNo readable elements{hint}. The app may not expose its controls; "
                "use inspect_screen to look at it."
            )
        summary = f"{len(chosen)} of {matched} elements" + (f" matching '{find}'" if find else "")
        if matched > len(chosen):
            summary += " (use find=... to narrow)"
        return "\n".join([header, summary, *lines])

    def _click(
        self, element_id: int | None, x: int | None, y: int | None, button: str, double: bool
    ) -> str:
        before = self._hwnds()
        if element_id is not None:
            ref = self._ref(element_id)
            win32.focus(ref.hwnd)
            left, top, right, bottom = self._u().rect(ref.element)
            if right > left and bottom > top:
                win32.click_at((left + right) // 2, (top + bottom) // 2, button, double)
                action = "Double-clicked" if double else "Clicked"
            elif button == "left" and not double and self._u().invoke(ref.element):
                action = "Activated"
            else:
                raise GuiError(f"[{element_id}] is not visible. Scroll to it or read the window.")
            target = f"[{element_id}] {describe(ref.info)}"
        elif x is not None and y is not None:
            win32.click_at(x, y, button, double)
            action, target = ("Double-clicked" if double else "Clicked"), f"({x}, {y})"
        else:
            raise GuiError("Give an element id from read_window, or x and y from inspect_screen.")
        return f"{action} {target}. {self._report(before)}"

    def _type(
        self, text: str, element_id: int | None, spec: str | None, replace: bool, enter: bool
    ) -> str:
        before = self._hwnds()
        ref: _Ref | None = None
        if element_id is not None:
            ref = self._ref(element_id)
            win32.focus(ref.hwnd)
            if not self._u().set_focus(ref.element):
                left, top, right, bottom = self._u().rect(ref.element)
                win32.click_at((left + right) // 2, (top + bottom) // 2)
        elif spec:
            win32.focus(self._resolve(spec).hwnd)
        if replace:
            win32.press_combo([_CTRL], ord("A"))
        elif ref is not None:
            win32.press_combo([_CTRL], _END)  # add to the end, not where the caret happened to be
        win32.type_text(text)
        if enter:
            win32.tap_key(_ENTER)
        time.sleep(0.2)
        shown = f"Typed {len(text)} characters"
        if ref is not None:
            shown += f" into [{element_id}] {describe(ref.info)}"
            value = self._u().value(ref.element) if self._u().alive(ref.element) else None
            if value is not None:
                clipped = value if len(value) <= 300 else value[:300] + "..."
                shown += f'. Its value is now: "{clipped}"'
        if enter:
            shown += " and pressed Enter"
        return f"{shown}. {self._report(before)}"

    def _press(self, keys: str, spec: str | None) -> str:
        combos = parse_keys(keys)
        before = self._hwnds()
        if spec:
            win32.focus(self._resolve(spec).hwnd)
        for modifiers, vk in combos:
            win32.press_combo(list(modifiers), vk)
            time.sleep(0.08)
        return f"Pressed {keys}. {self._report(before)}"

    def _scroll(self, direction: str, amount: int, element_id: int | None, spec: str | None) -> str:
        if element_id is not None:
            ref = self._ref(element_id)
            hwnd = ref.hwnd
            left, top, right, bottom = self._u().rect(ref.element)
        else:
            win = self._resolve(spec)
            hwnd = win.hwnd
            left, top, right, bottom = win.rect
        win32.focus(hwnd)
        clicks = amount if direction == "up" else -amount
        win32.scroll_at((left + right) // 2, (top + bottom) // 2, clicks)
        time.sleep(0.3)
        return (
            f"Scrolled {direction} by {amount}. Read the window again to see what is now visible."
        )

    def _focus(self, spec: str) -> str:
        win = self._resolve(spec)
        ok = win32.focus(win.hwnd)
        self._last_window = win.hwnd
        state = "is now in front" if ok else "could not be brought to the front"
        return f"Window {win32.window_info(win.hwnd).label()} {state}."

    def _window_action(self, spec: str, action: str) -> str:
        win = self._resolve(spec)
        before = self._hwnds()
        if action == "close":
            win32.close(win.hwnd)
            deadline = time.monotonic() + 3
            while win32.is_window(win.hwnd) and time.monotonic() < deadline:
                time.sleep(0.1)
            if win32.is_window(win.hwnd):
                return (
                    f"Asked {win.label()} to close, but it is still open (it may be asking to "
                    f"save). {self._report(before)}"
                )
            return f"Closed {win.label()}. {self._report(before)}"
        win32.show(win.hwnd, action)
        return f"{action.capitalize()}d {win.label()}. {self._report(before)}"

    def _start_apps(self, refresh: bool = False) -> list[StartApp]:
        if self._apps is None or refresh:
            self._apps = load_start_apps()
        return self._apps

    def _open_app(self, name: str) -> str:
        before = self._hwnds()
        path = Path(os.path.expandvars(name.strip().strip('"')))
        if path.suffix.lower() in {".exe", ".lnk", ".bat", ".cmd"} and path.exists():
            os.startfile(path)
            label = path.name
        else:
            app = match_app(name, self._start_apps()) or match_app(name, self._start_apps(True))
            if app is None:
                close = suggestions(name, self._start_apps())
                hint = f" Closest installed apps: {', '.join(close)}." if close else ""
                raise GuiError(f"No installed app matches '{name}'.{hint}")
            launch(app)
            label = app.name
        deadline = time.monotonic() + 15
        new: list[WindowInfo] = []
        while time.monotonic() < deadline:
            new = [w for w in win32.list_windows() if w.hwnd not in before]
            if new:
                break
            time.sleep(0.3)
        if not new:
            focused = next((w for w in win32.list_windows() if w.focused), None)
            where = focused.label() if focused else "none"
            return (
                f"Started {label}, but no new window appeared within 15 s (it may already be "
                f"open, or still loading). Focused window: {where}."
            )
        time.sleep(0.5)  # let it finish drawing before anyone reads it
        window = next((w for w in new if w.focused), new[0])
        self._last_window = window.hwnd
        return f"Started {label}. Window: {win32.window_info(window.hwnd).label()}."

    def _open_url(self, url: str) -> str:
        if urlparse(url).scheme not in {"http", "https"}:
            raise GuiError("Only http:// and https:// links can be opened.")
        before = self._hwnds()
        os.startfile(url)
        time.sleep(1.0)
        return f"Opened {url} in the default browser. {self._report(before)}"

    def _screenshot(self, spec: str | None) -> Shot:
        if not spec:
            return capture()
        win = self._resolve(spec)
        if win.minimized:
            win32.show(win.hwnd, "restore")
        win32.focus(win.hwnd)
        time.sleep(0.4)
        self._last_window = win.hwnd
        return capture(win32.window_info(win.hwnd).rect)
