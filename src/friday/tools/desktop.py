"""Tools that let the agent see and operate desktop apps (and web pages in the browser)."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, Field

from friday.agent.llm import LLM
from friday.desktop.automation import Desktop
from friday.desktop.vision import inspect
from friday.tools.registry import Risk, ToolRegistry, ToolResult

_WINDOW = "Window title, app name (e.g. 'Notepad', 'chrome') or window id from list_windows."


class ReadWindowArgs(BaseModel):
    window: str = Field(description=_WINDOW)
    find: str = Field(
        default="", description="Only list elements whose name or value contains this."
    )
    limit: int = Field(default=80, ge=10, le=400)


class InspectArgs(BaseModel):
    question: str = Field(description="What to read or find, e.g. 'Where is the Send button?'")
    window: str | None = Field(default=None, description=f"{_WINDOW} Omit for the whole screen.")


class OpenAppArgs(BaseModel):
    name: str = Field(
        description="App name as in the Start menu (e.g. 'Calculator'), or an .exe path."
    )


class OpenUrlArgs(BaseModel):
    url: str = Field(description="An http:// or https:// address.")


class FocusArgs(BaseModel):
    window: str = Field(description=_WINDOW)


class WindowActionArgs(BaseModel):
    window: str = Field(description=_WINDOW)
    action: Literal["minimize", "maximize", "restore", "close"]


class ClickArgs(BaseModel):
    element: int | None = Field(
        default=None, description="Element id from read_window (preferred)."
    )
    x: int | None = Field(default=None, description="Screen x, only from inspect_screen.")
    y: int | None = Field(default=None, description="Screen y, only from inspect_screen.")
    button: Literal["left", "right", "middle"] = "left"
    double: bool = False


class TypeArgs(BaseModel):
    text: str = Field(description="Text to type. New lines press Enter.")
    element: int | None = Field(
        default=None, description="Field to type into (id from read_window). Omit to type into "
        "whatever has keyboard focus.",
    )  # fmt: skip
    window: str | None = Field(default=None, description=f"{_WINDOW} Brought to front first.")
    replace: bool = Field(default=False, description="Select all and replace the existing text.")
    press_enter: bool = Field(default=False, description="Press Enter after typing.")


class KeysArgs(BaseModel):
    keys: str = Field(
        description="Key combos in order, e.g. 'ctrl+s', 'alt+tab', 'ctrl+a, delete', 'enter'."
    )
    window: str | None = Field(default=None, description=f"{_WINDOW} Brought to front first.")


class ScrollArgs(BaseModel):
    direction: Literal["up", "down"]
    amount: int = Field(default=5, ge=1, le=50, description="Mouse-wheel notches.")
    element: int | None = Field(default=None, description="Scroll over this element (id).")
    window: str | None = Field(default=None, description=f"{_WINDOW} Used if no element.")


def register_desktop_tools(registry: ToolRegistry, desktop: Desktop, llm: LLM) -> None:
    @registry.tool(group="apps", risk=Risk.READ)
    async def list_windows() -> str:
        """List open windows (id, title, app, which one is focused)."""
        return await desktop.list_windows()

    @registry.tool(group="apps", risk=Risk.READ, timeout_s=30)
    async def read_window(args: ReadWindowArgs) -> str:
        """Read a window's visible controls and text as a numbered list. Use the [ids] with click
        and type_text. Works for desktop apps and for web pages open in Chrome or Edge."""
        return await desktop.read_window(args.window, args.find, args.limit)

    @registry.tool(group="apps", risk=Risk.READ, timeout_s=90)
    async def inspect_screen(args: InspectArgs) -> ToolResult:
        """Look at a screenshot with vision, when read_window cannot see what you need (images,
        canvases, custom-drawn apps). Answers the question and gives click(x, y) positions."""
        shot = await desktop.screenshot(args.window)
        text, info = await inspect(llm, shot, args.question)
        return replace(ToolResult.success(text), calls=(info,))

    @registry.tool(group="apps", risk=Risk.WRITE, timeout_s=45)
    async def open_app(args: OpenAppArgs) -> str:
        """Start an installed app by name and wait for its window."""
        return await desktop.open_app(args.name)

    @registry.tool(group="apps", risk=Risk.WRITE, timeout_s=20)
    async def open_url(args: OpenUrlArgs) -> str:
        """Open a web page in the default browser."""
        return await desktop.open_url(args.url)

    @registry.tool(group="apps", risk=Risk.WRITE)
    async def focus_window(args: FocusArgs) -> str:
        """Bring a window to the front."""
        return await desktop.focus_window(args.window)

    @registry.tool(group="apps", risk=Risk.WRITE)
    async def window_action(args: WindowActionArgs) -> str:
        """Minimize, maximize, restore or close a window."""
        return await desktop.window_action(args.window, args.action)

    @registry.tool(group="apps", risk=Risk.WRITE)
    async def click(args: ClickArgs) -> str:
        """Click an element by id (preferred) or at screen x, y. Reports what changed on screen."""
        return await desktop.click(args.element, args.x, args.y, args.button, args.double)

    @registry.tool(group="apps", risk=Risk.WRITE)
    async def type_text(args: TypeArgs) -> str:
        """Type text as real keystrokes into a field or the focused control."""
        return await desktop.type_text(
            args.text, args.element, args.window, args.replace, args.press_enter
        )

    @registry.tool(group="apps", risk=Risk.WRITE)
    async def press_keys(args: KeysArgs) -> str:
        """Press keyboard shortcuts or keys, e.g. ctrl+s, enter, alt+tab."""
        return await desktop.press_keys(args.keys, args.window)

    @registry.tool(group="apps", risk=Risk.WRITE)
    async def scroll(args: ScrollArgs) -> str:
        """Scroll with the mouse wheel over an element or window."""
        return await desktop.scroll(args.direction, args.amount, args.element, args.window)
