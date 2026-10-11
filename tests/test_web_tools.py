from __future__ import annotations

import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from friday.tools import ToolRegistry
from friday.tools.web import download_page, page_text, register_web_tools
from tests.fakes import FakeLLM, allow_all, call, found

ARTICLE = """<!doctype html><html><head><title>Rates today</title>
<style>body { color: red }</style><script>var tracking = 1;</script></head>
<body><nav>Home | About</nav>
<article><h1>Dollar rate</h1><p>On 12 October 2026, 1 USD   =
96.78 INR.</p><p>Source: Reserve&nbsp;Bank.</p></article>
<noscript>Enable JavaScript</noscript></body></html>"""


@pytest.fixture
def site(tmp_path: Path) -> Iterator[str]:
    (tmp_path / "rates.html").write_text(ARTICLE, encoding="utf-8")
    (tmp_path / "data.json").write_text('{"usd_inr": 96.78}', encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None  # type: ignore[method-assign]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_page_text_keeps_readable_text_and_drops_scripts_and_styles() -> None:
    title, text = page_text(ARTICLE)
    assert title == "Rates today"
    assert "On 12 October 2026, 1 USD = 96.78 INR." in text
    assert "Source: Reserve Bank." in text
    assert "tracking" not in text and "color" not in text and "JavaScript" not in text


async def test_download_page_reads_html_and_text_and_refuses_the_rest(site: str) -> None:
    page = await download_page(f"{site}/rates.html", 6000)
    assert page.startswith('Page: "Rates today"') and "96.78 INR" in page
    assert "96.78" in await download_page(f"{site}/data.json", 6000)
    short = await download_page(f"{site}/rates.html", 500)
    assert len(short) < 700
    with pytest.raises(ValueError, match="not a web page"):
        await download_page(f"{site}/image.png", 6000)
    with pytest.raises(ValueError, match="HTTP 404"):
        await download_page(f"{site}/missing.html", 6000)
    with pytest.raises(ValueError, match="Only http"):
        await download_page("file:///C:/Windows/win.ini", 6000)


async def test_web_search_tool_returns_a_sourced_answer_in_one_call() -> None:
    llm = FakeLLM().queue(found("1 USD = 96.78 INR (12 Oct 2026).", "XE", "Wise"))
    registry = ToolRegistry(allow_all)
    register_web_tools(registry, llm)
    result = await registry.execute(call("web_search", query="USD to INR"))
    assert result.ok and llm.web_searches == 1 and len(result.calls) == 1
    assert "96.78 INR" in result.output
    assert "- XE: https://xe/page" in result.output and "fetch_page" in result.output


async def test_fetch_page_tool_reads_a_page(site: str) -> None:
    registry = ToolRegistry(allow_all)
    register_web_tools(registry, FakeLLM())
    result = await registry.execute(call("fetch_page", url=f"{site}/rates.html"))
    assert result.ok and "96.78 INR" in result.output
    assert registry.get("fetch_page") is not None and registry.groups() == ["web"]
