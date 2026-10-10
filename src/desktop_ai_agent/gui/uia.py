"""Windows UI Automation client. Every method must run on the GUI worker thread."""

from __future__ import annotations

import time
from functools import reduce
from typing import Any

import comtypes  # pyright: ignore[reportMissingTypeStubs]
import comtypes.client  # pyright: ignore[reportMissingTypeStubs]

from desktop_ai_agent.gui.elements import KINDS, ElementInfo

_TREE_SCOPE_DESCENDANTS = 4
_NAME, _CONTROL_TYPE, _RECT, _ENABLED, _OFFSCREEN = 30005, 30003, 30001, 30010, 30022
_HAS_FOCUS, _VALUE, _TOGGLE, _SELECTED = 30008, 30045, 30086, 30079
_INVOKE, _VALUE_PATTERN, _EXPAND, _SELECTION_ITEM, _TOGGLE_PATTERN = (
    10000,
    10002,
    10005,
    10010,
    10015,
)
_WARMUP_THRESHOLD = 8  # Chromium/Electron apps expose almost nothing until first asked


class Uia:
    def __init__(self) -> None:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)  # pyright: ignore[reportUnknownMemberType]
        comtypes.client.GetModule("UIAutomationCore.dll")  # pyright: ignore[reportUnknownMemberType]
        from comtypes.gen import UIAutomationClient  # pyright: ignore

        self._u: Any = UIAutomationClient
        self._auto: Any = comtypes.client.CreateObject(  # pyright: ignore[reportUnknownMemberType]
            self._u.CUIAutomation, interface=self._u.IUIAutomation
        )
        auto = self._auto
        kinds = [auto.CreatePropertyCondition(_CONTROL_TYPE, kind) for kind in KINDS]
        any_kind = reduce(auto.CreateOrCondition, kinds)
        visible = auto.CreatePropertyCondition(_OFFSCREEN, False)
        self._condition = auto.CreateAndCondition(any_kind, visible)
        self._cache = auto.CreateCacheRequest()
        for prop in (_NAME, _CONTROL_TYPE, _RECT, _ENABLED, _HAS_FOCUS, _VALUE, _TOGGLE, _SELECTED):
            self._cache.AddProperty(prop)
        self._warmed: set[int] = set()

    # ---- reading ---------------------------------------------------------------------------

    def snapshot(self, hwnd: int) -> list[tuple[Any, ElementInfo]]:
        items = self._find_all(hwnd)
        if hwnd not in self._warmed:
            self._warmed.add(hwnd)
            if len(items) < _WARMUP_THRESHOLD:
                time.sleep(0.8)
                items = self._find_all(hwnd)
        return items

    def _find_all(self, hwnd: int) -> list[tuple[Any, ElementInfo]]:
        root = self._auto.ElementFromHandle(hwnd)
        found = root.FindAllBuildCache(_TREE_SCOPE_DESCENDANTS, self._condition, self._cache)
        items: list[tuple[Any, ElementInfo]] = []
        for i in range(found.Length):
            element = found.GetElement(i)
            items.append((element, self._info(element)))
        return items

    def _info(self, element: Any) -> ElementInfo:
        rect = element.CachedBoundingRectangle
        value = element.GetCachedPropertyValue(_VALUE)
        toggle = element.GetCachedPropertyValue(_TOGGLE)
        selected = element.GetCachedPropertyValue(_SELECTED)
        return ElementInfo(
            kind=KINDS.get(element.CachedControlType, "other"),
            name=element.CachedName or "",
            value=value if isinstance(value, str) else "",
            enabled=bool(element.CachedIsEnabled),
            focused=bool(element.CachedHasKeyboardFocus),
            toggled=(toggle == 1)
            if isinstance(toggle, int) and not isinstance(toggle, bool)
            else None,
            selected=selected if isinstance(selected, bool) else None,
            rect=(rect.left, rect.top, rect.right, rect.bottom),
        )

    # ---- element state ---------------------------------------------------------------------

    def alive(self, element: Any) -> bool:
        try:
            element.CurrentProcessId  # noqa: B018 - raises if the element is gone
            return True
        except Exception:
            return False

    def rect(self, element: Any) -> tuple[int, int, int, int]:
        r = element.CurrentBoundingRectangle
        return r.left, r.top, r.right, r.bottom

    def value(self, element: Any) -> str | None:
        pattern = self._pattern(element, _VALUE_PATTERN, "IUIAutomationValuePattern")
        if pattern is None:
            return None
        try:
            return str(pattern.CurrentValue)
        except Exception:
            return None

    # ---- actions ---------------------------------------------------------------------------

    def set_focus(self, element: Any) -> bool:
        try:
            element.SetFocus()
            return True
        except Exception:
            return False

    def invoke(self, element: Any) -> bool:
        """Activate without the mouse: invoke, toggle, select or expand, whichever it supports."""
        for pattern_id, interface, method in (
            (_INVOKE, "IUIAutomationInvokePattern", "Invoke"),
            (_TOGGLE_PATTERN, "IUIAutomationTogglePattern", "Toggle"),
            (_SELECTION_ITEM, "IUIAutomationSelectionItemPattern", "Select"),
            (_EXPAND, "IUIAutomationExpandCollapsePattern", "Expand"),
        ):
            pattern = self._pattern(element, pattern_id, interface)
            if pattern is not None:
                getattr(pattern, method)()
                return True
        return False

    def _pattern(self, element: Any, pattern_id: int, interface: str) -> Any:
        try:
            unknown = element.GetCurrentPattern(pattern_id)
        except Exception:
            return None
        if not unknown:
            return None
        return unknown.QueryInterface(getattr(self._u, interface))
