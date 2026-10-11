"""Friday's own browser window, driven by Playwright: start, stop, open and read pages, tabs.

It uses the installed Chrome (or Edge) with a separate profile, so the user's normal browser is
untouched while sign-ins made in Friday's browser are remembered between sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeout,
)

from friday.browser.snapshot import SNAPSHOT_JS, format_snapshot

_FULL = {"limit": 100, "text": 2500}
_AFTER_ACTION = {"limit": 30, "text": 800}  # enough to see the effect, cheap on tokens


class BrowserError(Exception):
    pass


class Browser:
    def __init__(
        self,
        profile_dir: Path,
        *,
        channels: Sequence[str] = ("chrome", "msedge"),
        headless: bool = False,
    ) -> None:
        self._profile = profile_dir
        self._channels = list(channels)
        self._headless = headless
        self._pw: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._names: dict[int, str] = {}
        self._lock = asyncio.Lock()

    def describe_element(self, element_id: int) -> str | None:
        """Name of an element from the latest snapshot (used by the safety policy)."""
        return self._names.get(element_id)

    # ---- lifecycle --------------------------------------------------------------------------

    async def _ensure(self) -> Page:
        if self._context is None:
            self._profile.mkdir(parents=True, exist_ok=True)
            self._pw = await async_playwright().start()
            errors: list[str] = []
            for channel in self._channels:
                try:
                    self._context = await self._pw.chromium.launch_persistent_context(
                        str(self._profile),
                        channel=channel,
                        headless=self._headless,
                        no_viewport=True,
                        args=["--start-maximized"],
                    )
                    break
                except Exception as exc:
                    errors.append(f"{channel}: {str(exc).splitlines()[0][:160]}")
            if self._context is None:
                await self._pw.stop()
                self._pw = None
                raise BrowserError(
                    "Could not start a browser (is another Friday browser already open?). "
                    + " | ".join(errors)
                )
            self._context.on("page", self._on_new_page)
            self._page = self._context.pages[0] if self._context.pages else None
        assert self._context is not None
        if self._page is None or self._page.is_closed():
            open_pages = [p for p in self._context.pages if not p.is_closed()]
            self._page = open_pages[-1] if open_pages else await self._context.new_page()
        return self._page

    @contextlib.asynccontextmanager
    async def page(self) -> AsyncGenerator[Page]:
        """The current tab, held exclusively so only one browser action runs at a time."""
        async with self._lock:
            yield await self._ensure()

    def _on_new_page(self, page: Page) -> None:
        self._page = page  # links that open a new tab: follow them

    async def close(self) -> None:
        context, pw = self._context, self._pw
        self._context, self._pw, self._page = None, None, None
        if context is not None:
            with contextlib.suppress(Exception):
                await context.close()
        if pw is not None:
            await pw.stop()

    # ---- helpers ----------------------------------------------------------------------------

    async def _settle(self, page: Page) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            await page.wait_for_load_state("networkidle", timeout=1_200)
        except PlaywrightTimeout:
            pass  # busy pages never go fully idle; what has loaded is enough to read

    async def _snapshot(self, page: Page, *, find: str = "", limit: int, text: int) -> str:
        data: dict[str, Any] = await page.evaluate(
            SNAPSHOT_JS, {"limit": limit, "find": find, "textLimit": text}
        )
        self._names = {
            int(item["id"]): f"{item['kind']} {item.get('name', '')}".strip()
            for item in data.get("items", [])
        }
        return format_snapshot(data, find=find, with_text=text > 0)

    async def element(self, page: Page, element_id: int) -> Any:
        locator = page.locator(f'[data-friday-id="{element_id}"]')
        if await locator.count() == 0:
            raise BrowserError(
                f"Element [{element_id}] is not on the page any more. Read the page again."
            )
        return locator.first

    async def after(self, page: Page, note: str) -> str:
        """Report an action's effect: a short snapshot of the page (or of a tab it opened)."""
        await self._settle(page)
        current = await self._ensure()  # may be a tab the action just opened
        if current is not page:
            note += " It opened a new tab."
        return f"{note}\n{await self._snapshot(current, **_AFTER_ACTION)}"  # type: ignore[arg-type]

    # ---- pages and tabs ---------------------------------------------------------------------

    async def open(self, url: str, new_tab: bool) -> str:
        target = url.strip()
        if "://" not in target:
            target = "https://" + target
        if urlparse(target).scheme not in {"http", "https"}:
            raise BrowserError("Only http:// and https:// pages can be opened.")
        async with self.page() as page:
            if new_tab:
                page = await page.context.new_page()
                self._page = page
            try:
                await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            except PlaywrightTimeout as exc:
                raise BrowserError(f"The page took too long to load: {target}") from exc
            await self._settle(page)
            return await self._snapshot(page, limit=_FULL["limit"], text=_FULL["text"])

    async def read(self, find: str, limit: int, with_text: bool) -> str:
        async with self.page() as page:
            return await self._snapshot(
                page, find=find, limit=limit, text=_FULL["text"] if with_text else 0
            )

    async def tabs(self, action: str, index: int | None) -> str:
        async with self.page() as page:
            context = page.context
            pages = [p for p in context.pages if not p.is_closed()]
            if action == "new":
                self._page = await context.new_page()
                return f"Opened tab {len(pages)}. Use browser_open to load a page in it."
            if action in {"switch", "close"}:
                if index is None or not 0 <= index < len(pages):
                    raise BrowserError(f"Give a tab index between 0 and {len(pages) - 1}.")
                target = pages[index]
                if action == "close":
                    await target.close()
                    self._page = None
                    return f"Closed tab {index}."
                self._page = target
                await target.bring_to_front()
                return await self.after(target, f"Switched to tab {index}.")
            lines: list[str] = []
            for i, p in enumerate(pages):
                marker = " (current)" if p is self._page else ""
                lines.append(f'{i}: "{await p.title()}" {p.url}{marker}')
            return "\n".join(lines) or "No tabs."
