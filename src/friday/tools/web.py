"""Internet tools: search the web with Google (through Gemini) and read web pages directly.

Both are read-only and much faster than driving the browser; the browser tools stay for sites
that need clicks, logins or forms.
"""

from __future__ import annotations

import re
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from friday.agent import prompts
from friday.agent.llm import LLM, user_text
from friday.tools.registry import Risk, ToolRegistry, ToolResult

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)
_MAX_BYTES = 3_000_000
_SPACES = re.compile(r"\s+")
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "br", "li", "ul", "ol", "tr", "table", "h1", "h2",
    "h3", "h4", "h5", "h6", "blockquote", "pre", "header", "footer", "nav", "aside", "figure",
}  # fmt: skip


class WebSearchArgs(BaseModel):
    query: str = Field(description="What to find out, as a precise search query or question.")


class FetchPageArgs(BaseModel):
    url: str = Field(description="Web address, e.g. example.com/page or https://example.com.")
    max_chars: int = Field(default=6000, ge=500, le=20000, description="How much text to return.")


class _TextExtractor(HTMLParser):
    """Collects a page's title and readable text, skipping scripts, styles and markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._chunks: list[str] = []
        self._skipping = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in _SKIP_TAGS:
            self._skipping += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skipping:
            self._chunks.append(_SPACES.sub(" ", data))  # line breaks in the source are spaces

    @property
    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._chunks).splitlines())
        return "\n".join(line for line in lines if line)


def page_text(html: str) -> tuple[str, str]:
    """(title, readable text) of an HTML page."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return " ".join(parser.title.split()), parser.text


async def download_page(url: str, max_chars: int) -> str:
    target = url.strip()
    if "://" not in target:
        target = "https://" + target
    if urlparse(target).scheme not in {"http", "https"}:
        raise ValueError("Only http:// and https:// pages can be read.")
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=20, headers={"User-Agent": _USER_AGENT}
    ) as client:
        resp = await client.get(target)
    if resp.status_code >= 400:
        raise ValueError(f"The site answered HTTP {resp.status_code} for {resp.url}.")
    kind = resp.headers.get("content-type", "").lower()
    if len(resp.content) > _MAX_BYTES:
        raise ValueError("The page is too large to read; open it with browser_open instead.")
    if "html" in kind:
        title, text = page_text(resp.text)
    elif kind.startswith("text/") or "json" in kind or "xml" in kind:
        title, text = "", resp.text.strip()
    else:
        raise ValueError(f"This is not a web page ({kind or 'unknown type'}); it cannot be read.")
    if not text:
        raise ValueError(
            "The page has no readable text (it may need JavaScript); use browser_open."
        )
    clipped = text if len(text) <= max_chars else text[:max_chars] + "\n...[more text on the page]"
    header = f'Page: "{title}" {resp.url}' if title else f"Page: {resp.url}"
    return f"{header}\n\n{clipped}"


def register_web_tools(registry: ToolRegistry, llm: LLM) -> None:
    @registry.tool(group="web", risk=Risk.READ, timeout_s=60)
    async def web_search(args: WebSearchArgs) -> ToolResult:
        """Search the web (live Google results) and get a short, sourced answer in one call. Use
        it for any fact that can change: news, prices, rates, weather, releases, people."""
        resp = await llm.generate(
            system=prompts.SEARCH_SYSTEM,
            contents=[user_text(f"Question: {args.query}")],
            web_search=True,
        )
        lines = [resp.text or "No answer found in the search results."]
        if resp.sources:
            lines.append("Sources (read one in full with fetch_page):")
            lines += [f"- {s.title}: {s.url}" for s in resp.sources[:6]]
        result = ToolResult.success("\n".join(lines))
        return replace(result, calls=(resp.info,), calls_purpose="search")

    @registry.tool(group="web", risk=Risk.READ, timeout_s=30)
    async def fetch_page(args: FetchPageArgs) -> str:
        """Read a web page's text directly, without opening the browser (articles, docs, source
        links from web_search). Pages that need JavaScript or a login need browser_open."""
        return await download_page(args.url, args.max_chars)
