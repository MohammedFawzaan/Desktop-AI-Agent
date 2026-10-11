from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from friday.browser import Browser, BrowserError, actions
from friday.browser.snapshot import KeysError, format_snapshot, to_playwright_keys
from friday.safety import SafetyPolicy
from friday.tools.browser import BrowserClickArgs


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("enter", ["Enter"]),
        ("ctrl+l", ["Control+l"]),
        ("Ctrl + Shift + T", ["Control+Shift+t"]),
        ("escape, tab", ["Escape", "Tab"]),
        ("f5", ["F5"]),
    ],
)
def test_keys_map_to_playwright_names(spec: str, expected: list[str]) -> None:
    assert to_playwright_keys(spec) == expected


@pytest.mark.parametrize("spec", ["", "hyper+x", "ctrl+banana"])
def test_bad_keys_are_rejected(spec: str) -> None:
    with pytest.raises(KeysError):
        to_playwright_keys(spec)


def test_snapshot_formatting() -> None:
    data = {
        "title": "Shop",
        "url": "https://shop.test/",
        "matched": 5,
        "items": [
            {"id": 1, "kind": "heading", "name": "Welcome", "value": ""},
            {"id": 2, "kind": "link", "name": "Docs", "value": "", "href": "/docs"},
            {"id": 3, "kind": "input", "name": "Search", "value": "shoes"},
            {"id": 4, "kind": "checkbox", "name": "Gift wrap", "value": "", "checked": False},
        ],
        "text": "Welcome to the shop",
        "textLength": 5000,
    }
    out = format_snapshot(data)
    assert out.splitlines()[0] == 'Page: "Shop" https://shop.test/'
    assert "4 of 5 elements (use find=... to narrow)" in out
    assert '[2] link "Docs" -> /docs' in out
    assert '[3] input "Search" = "shoes"' in out
    assert '[4] checkbox "Gift wrap" [unchecked]' in out
    assert "more characters" in out
    assert "Page text:" not in format_snapshot(data, with_text=False)


def test_risky_web_clicks_ask_first() -> None:
    names = {1: "button Add to cart", 2: "button Place order", 3: "link Delete account"}
    policy = SafetyPolicy(None, describe_web_element=names.get)
    assert policy.assess("browser_click", BrowserClickArgs(element=1)).level == "allow"
    assert policy.assess("browser_click", BrowserClickArgs(element=2)).level == "danger"
    assert policy.assess("browser_click", BrowserClickArgs(element=3)).level == "danger"


PAGE = """<!doctype html><html><head><title>Friday test page</title></head><body>
<h1>Hello from the test page</h1>
<a href="/other.html">Go to other page</a>
<label for="q">Search</label><input id="q" name="q">
<button onclick="run()">Search now</button>
<script>
function run() {
  const q = document.getElementById('q').value;
  document.getElementById('out').textContent = 'You searched: ' + q;
}
</script>
<div id="out"></div>
<select id="size"><option>Small</option><option>Large</option></select>
<input type="checkbox" id="gift"><label for="gift">Gift wrap</label>
<button style="display:none">Hidden button</button>
</body></html>"""


@pytest.fixture
def site(tmp_path: Path) -> Iterator[str]:
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "other.html").write_text(
        "<title>Other</title><h1>The other page</h1>", encoding="utf-8"
    )
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None  # type: ignore[method-assign]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()


def element_id(snapshot: str, kind: str, name: str) -> int:
    match = re.search(rf'\[(\d+)\] {kind} "{re.escape(name)}"', snapshot)
    assert match, f"{kind} {name} not in snapshot:\n{snapshot}"
    return int(match.group(1))


async def test_real_browser_open_read_type_click_select_and_navigate(
    site: str, tmp_path: Path
) -> None:
    browser = Browser(tmp_path / "profile", headless=True)
    try:
        try:
            page = await browser.open(site, new_tab=False)
        except BrowserError as exc:
            pytest.skip(f"No Chrome or Edge available: {exc}")
        assert 'Page: "Friday test page"' in page
        assert 'heading "Hello from the test page"' in page
        assert "Hidden button" not in page

        search = element_id(page, "input", "Search")
        typed = await actions.type_text(browser, search, "red shoes", submit=False, replace=True)
        assert '= "red shoes"' in typed

        clicked = await actions.click(
            browser, element_id(typed, "button", "Search now"), double=False
        )
        assert "You searched: red shoes" in clicked

        dropdown = int(re.search(r"\[(\d+)\] select", clicked).group(1))  # type: ignore[union-attr]
        selected = await actions.select(browser, dropdown, "Large")
        assert '= "Large"' in selected

        box = element_id(selected, "checkbox", "Gift wrap")
        assert "[checked]" in await actions.click(browser, box, double=False)

        moved = await actions.click(
            browser, element_id(page, "link", "Go to other page"), double=False
        )
        assert 'Page: "Other"' in moved
        back = await actions.back(browser)
        assert 'Page: "Friday test page"' in back
        assert '0: "Friday test page"' in await browser.tabs("list", None)

        with pytest.raises(BrowserError, match="not on the page"):
            await actions.click(browser, 9999, double=False)
    finally:
        await browser.close()
