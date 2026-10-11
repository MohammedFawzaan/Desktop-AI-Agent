from __future__ import annotations

import pytest

from friday.desktop.apps import StartApp, match_app, suggestions
from friday.desktop.elements import ElementInfo, describe, select
from friday.desktop.input import KeyParseError, normalize, parse_keys
from friday.desktop.screen import Shot, box_to_point

CTRL, ALT, SHIFT, WIN = 0x11, 0x12, 0x10, 0x5B


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("ctrl+s", [((CTRL,), ord("S"))]),
        ("Ctrl + Shift + T", [((CTRL, SHIFT), ord("T"))]),
        ("alt+f4", [((ALT,), 0x73)]),
        ("enter", [((), 0x0D)]),
        ("win", [((), WIN)]),
        ("ctrl+a, delete", [((CTRL,), ord("A")), ((), 0x2E)]),
        ("ctrl+plus", [((CTRL,), 0xBB)]),
        ("2, *, num4", [((), ord("2")), ((), 0x6A), ((), 0x64)]),
    ],
)
def test_parse_keys(spec: str, expected: list[tuple[tuple[int, ...], int]]) -> None:
    assert parse_keys(spec) == expected


@pytest.mark.parametrize("spec", ["", "hyper+x", "ctrl+banana", "ctrl+f30", " , "])
def test_bad_keys_are_rejected(spec: str) -> None:
    with pytest.raises(KeyParseError):
        parse_keys(spec)


def test_normalize_for_safety_checks() -> None:
    assert normalize("Alt + F4") == ["alt+f4"]
    assert normalize("ctrl+a, Shift+Delete") == ["ctrl+a", "shift+delete"]


def test_elements_are_described_compactly() -> None:
    assert describe(ElementInfo("button", "Save")) == 'button "Save"'
    edit = ElementInfo("edit", "Search", value="cats", focused=True)
    assert describe(edit) == 'edit "Search" = "cats" [focused]'
    box = ElementInfo("checkbox", "Wrap", toggled=True, enabled=False)
    assert describe(box) == 'checkbox "Wrap" [on] [disabled]'


def test_select_skips_noise_dedupes_labels_and_respects_find_and_limit() -> None:
    items = [
        ElementInfo("button", "Save"),
        ElementInfo("text", "Save"),  # label repeating the button: dropped
        ElementInfo("button", ""),  # nameless button: dropped
        ElementInfo("edit", ""),  # nameless edit: kept, you can type into it
        ElementInfo("link", "Open settings"),
        ElementInfo("text", "Hello world"),
    ]
    chosen, matched = select(items)
    assert chosen == [0, 3, 4, 5] and matched == 4
    assert select(items, find="SETTINGS") == ([4], 1)
    assert select(items, limit=2) == ([0, 3], 4)


APPS = [
    StartApp("Calculator", "calc!App"),
    StartApp("Paint", "paint!App"),
    StartApp("Paint 3D Preview", "p3d!App"),
    StartApp("Visual Studio Code", "code"),
    StartApp("Google Chrome", "chrome"),
]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("calculator", "Calculator"),
        ("Paint", "Paint"),
        ("chrome", "Google Chrome"),
        ("visual studio", "Visual Studio Code"),
        ("calculater", "Calculator"),  # a typo or mis-heard name still matches
        ("chrome.exe", "Google Chrome"),
    ],
)
def test_app_names_match_like_a_person_would_say_them(query: str, expected: str) -> None:
    app = match_app(query, APPS)
    assert app is not None and app.name == expected


def test_unknown_apps_get_suggestions_instead_of_a_wrong_match() -> None:
    assert match_app("photoshop", APPS) is None
    assert match_app("", APPS) is None
    assert "Paint" in suggestions("paintt", APPS)


def test_vision_boxes_map_back_to_screen_pixels() -> None:
    shot = Shot(b"", left=100, top=50, width=1000, height=500)
    assert box_to_point([0, 0, 1000, 1000], shot) == (600, 300)  # centre of the region
    assert box_to_point([100, 200, 300, 400], shot) == (400, 150)
    assert box_to_point([-50, -50, 2000, 2000], shot) == (600, 300)  # clamped to the grid
