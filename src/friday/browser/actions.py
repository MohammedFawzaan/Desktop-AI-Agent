"""What Friday does on a web page: click, type, press keys, pick options, scroll and go back.

Each action reports its effect with a short snapshot of the page, so the agent sees the result.
"""

from __future__ import annotations

import asyncio

from friday.browser.session import Browser
from friday.browser.snapshot import to_playwright_keys


async def click(browser: Browser, element_id: int, double: bool) -> str:
    async with browser.page() as page:
        element = await browser.element(page, element_id)
        name = browser.describe_element(element_id) or ""
        if double:
            await element.dblclick(timeout=8_000)
        else:
            await element.click(timeout=8_000)
        return await browser.after(page, f"Clicked [{element_id}] {name}.")


async def type_text(
    browser: Browser, element_id: int, text: str, submit: bool, replace: bool
) -> str:
    async with browser.page() as page:
        element = await browser.element(page, element_id)
        if replace:
            await element.fill(text, timeout=8_000)
        else:
            await element.click(timeout=8_000)
            await page.keyboard.press("End")
            await page.keyboard.type(text)
        note = f"Typed into [{element_id}] {browser.describe_element(element_id) or ''}."
        if submit:
            await element.press("Enter")
            note += " Pressed Enter."
        return await browser.after(page, note)


async def press(browser: Browser, keys: str) -> str:
    combos = to_playwright_keys(keys)
    async with browser.page() as page:
        for combo in combos:
            await page.keyboard.press(combo)
        return await browser.after(page, f"Pressed {keys}.")


async def select(browser: Browser, element_id: int, option: str) -> str:
    async with browser.page() as page:
        element = await browser.element(page, element_id)
        try:
            await element.select_option(label=option, timeout=5_000)
        except Exception:
            await element.select_option(value=option, timeout=5_000)
        return await browser.after(page, f"Selected '{option}' in [{element_id}].")


async def scroll(browser: Browser, direction: str, pages: int) -> str:
    async with browser.page() as page:
        height = await page.evaluate("() => window.innerHeight")
        delta = int(height * 0.9) * pages * (1 if direction == "down" else -1)
        await page.mouse.wheel(0, delta)
        await asyncio.sleep(0.6)
        return await browser.after(page, f"Scrolled {direction} {pages} page(s).")


async def back(browser: Browser) -> str:
    async with browser.page() as page:
        await page.go_back(wait_until="domcontentloaded", timeout=15_000)
        return await browser.after(page, "Went back.")
