"""Turn UI Automation elements into a short, numbered list the model can act on."""

from __future__ import annotations

from dataclasses import dataclass

# UI Automation control type ids -> short readable kinds. Only these are shown to the agent.
KINDS: dict[int, str] = {
    50000: "button",
    50002: "checkbox",
    50003: "combobox",
    50004: "edit",
    50005: "link",
    50007: "listitem",
    50011: "menuitem",
    50013: "radio",
    50015: "slider",
    50016: "spinner",
    50019: "tab",
    50020: "text",
    50024: "treeitem",
    50029: "dataitem",
    50030: "document",
    50031: "splitbutton",
    50035: "header",
}
_MAX_NAME = 120
_MAX_VALUE = 80


@dataclass(frozen=True, slots=True)
class ElementInfo:
    kind: str
    name: str
    value: str = ""
    enabled: bool = True
    focused: bool = False
    toggled: bool | None = None
    selected: bool | None = None
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return (left + right) // 2, (top + bottom) // 2

    @property
    def has_area(self) -> bool:
        left, top, right, bottom = self.rect
        return right > left and bottom > top


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def describe(info: ElementInfo) -> str:
    parts = [info.kind]
    if info.name:
        parts.append(f'"{_clip(info.name, _MAX_NAME)}"')
    if info.value and info.value != info.name:
        parts.append(f'= "{_clip(info.value, _MAX_VALUE)}"')
    if info.toggled is not None:
        parts.append("[on]" if info.toggled else "[off]")
    if info.selected:
        parts.append("[selected]")
    if info.focused:
        parts.append("[focused]")
    if not info.enabled:
        parts.append("[disabled]")
    return " ".join(parts)


def is_useful(info: ElementInfo) -> bool:
    """Skip nameless decoration; keep anything you can type into even if it has no label."""
    if info.kind in ("edit", "document", "combobox"):
        return True
    return bool(info.name.strip())


def select(items: list[ElementInfo], find: str = "", limit: int = 120) -> tuple[list[int], int]:
    """Indexes of the elements to show (filtered, de-duplicated) and how many matched in total."""
    needle = find.lower().strip()
    chosen: list[int] = []
    recent: list[str] = []
    matched = 0
    for index, info in enumerate(items):
        if not is_useful(info):
            continue
        if needle and needle not in info.name.lower() and needle not in info.value.lower():
            continue
        # A label that repeats the button right before it adds nothing.
        if info.kind == "text" and info.name in recent:
            continue
        recent = [*recent[-2:], info.name]
        matched += 1
        if len(chosen) < limit:
            chosen.append(index)
    return chosen, matched
