"""Tools for websites, using Friday's own Playwright-driven browser."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from friday.browser import actions
from friday.browser.session import Browser
from friday.tools.registry import Risk, ToolRegistry


class BrowserOpenArgs(BaseModel):
    url: str = Field(description="Web address, e.g. example.com or https://example.com.")
    new_tab: bool = Field(default=False, description="Open in a new tab.")


class BrowserReadArgs(BaseModel):
    find: str = Field(default="", description="Only list elements whose text contains this.")
    limit: int = Field(default=100, ge=10, le=500)
    include_text: bool = Field(default=True, description="Also return the page's visible text.")


class BrowserClickArgs(BaseModel):
    element: int = Field(description="Element id from the latest page snapshot.")
    double: bool = False


class BrowserTypeArgs(BaseModel):
    element: int = Field(description="Id of the input, text area or editor.")
    text: str
    submit: bool = Field(default=False, description="Press Enter afterwards (e.g. to search).")
    replace: bool = Field(default=True, description="Replace existing text; false appends.")


class BrowserKeysArgs(BaseModel):
    keys: str = Field(description="Key combos in order, e.g. 'enter', 'ctrl+a', 'escape, tab'.")


class BrowserSelectArgs(BaseModel):
    element: int = Field(description="Id of the dropdown (select).")
    option: str = Field(description="Visible text (or value) of the option to choose.")


class BrowserScrollArgs(BaseModel):
    direction: Literal["up", "down"]
    pages: int = Field(default=1, ge=1, le=10)


class BrowserTabsArgs(BaseModel):
    action: Literal["list", "switch", "close", "new"] = "list"
    index: int | None = Field(default=None, description="Tab number from the list (switch/close).")


def register_browser_tools(registry: ToolRegistry, browser: Browser) -> None:
    @registry.tool(group="web", risk=Risk.WRITE, timeout_s=60)
    async def browser_open(args: BrowserOpenArgs) -> str:
        """Open a web page in Friday's browser and return its links, buttons, inputs ([ids]) and
        text. Use this for anything you need to do or read on a website."""
        return await browser.open(args.url, args.new_tab)

    @registry.tool(group="web", risk=Risk.READ, timeout_s=30)
    async def browser_read(args: BrowserReadArgs) -> str:
        """Read the current page again: numbered elements and visible text."""
        return await browser.read(args.find, args.limit, args.include_text)

    @registry.tool(group="web", risk=Risk.WRITE, timeout_s=45)
    async def browser_click(args: BrowserClickArgs) -> str:
        """Click a link or button on the page by id; returns the updated page."""
        return await actions.click(browser, args.element, args.double)

    @registry.tool(group="web", risk=Risk.WRITE, timeout_s=45)
    async def browser_type(args: BrowserTypeArgs) -> str:
        """Type into an input on the page by id; optionally press Enter to submit."""
        return await actions.type_text(browser, args.element, args.text, args.submit, args.replace)

    @registry.tool(group="web", risk=Risk.WRITE, timeout_s=30)
    async def browser_press(args: BrowserKeysArgs) -> str:
        """Press keys in the browser page."""
        return await actions.press(browser, args.keys)

    @registry.tool(group="web", risk=Risk.WRITE, timeout_s=30)
    async def browser_select(args: BrowserSelectArgs) -> str:
        """Choose an option in a dropdown by id."""
        return await actions.select(browser, args.element, args.option)

    @registry.tool(group="web", risk=Risk.WRITE, timeout_s=30)
    async def browser_scroll(args: BrowserScrollArgs) -> str:
        """Scroll the page (useful for pages that load more content as you scroll)."""
        return await actions.scroll(browser, args.direction, args.pages)

    @registry.tool(group="web", risk=Risk.WRITE, timeout_s=30)
    async def browser_back() -> str:
        """Go back to the previous page."""
        return await actions.back(browser)

    @registry.tool(group="web", risk=Risk.WRITE, timeout_s=30)
    async def browser_tabs(args: BrowserTabsArgs) -> str:
        """List, switch, open or close tabs in Friday's browser."""
        return await browser.tabs(args.action, args.index)
