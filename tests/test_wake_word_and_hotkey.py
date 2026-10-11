from __future__ import annotations

import pytest

from friday.voice.hotkey import HotkeyError, HotkeyListener, parse_hotkey
from friday.voice.text_to_speech import speakable
from friday.voice.wake_word import is_wake_text, strip_wake_phrase


@pytest.mark.parametrize(
    ("spec", "flags", "vk"),
    [
        ("ctrl+alt+f", 0x2 | 0x1, ord("F")),
        ("Ctrl + Shift + Space", 0x2 | 0x4, 0x20),
        ("win+f9", 0x8, 0x78),
        ("alt+1", 0x1, ord("1")),
    ],
)
def test_parse_hotkey(spec: str, flags: int, vk: int) -> None:
    assert parse_hotkey(spec) == (flags, vk)


@pytest.mark.parametrize("spec", ["f", "ctrl", "ctrl+", "hyper+f", "ctrl+banana", "ctrl+f99"])
def test_bad_hotkeys_are_rejected(spec: str) -> None:
    with pytest.raises(HotkeyError):
        parse_hotkey(spec)


def test_hotkey_can_be_registered_and_released() -> None:
    listener = HotkeyListener("ctrl+alt+shift+f23", lambda: None)
    listener.start()
    listener.stop()
    again = HotkeyListener("ctrl+alt+shift+f23", lambda: None)
    again.start()  # the first registration was released cleanly
    again.stop()


def test_wake_phrase_detection() -> None:
    assert is_wake_text("hey friday")
    assert is_wake_text("  Hey   Friday ")
    assert is_wake_text("hey friday [unk]")
    assert not is_wake_text("friday")
    assert not is_wake_text("hey")
    assert not is_wake_text("[unk]")


def test_wake_phrase_is_stripped_from_the_start_of_a_transcript_only() -> None:
    assert strip_wake_phrase("Hey Friday, open notepad") == "open notepad"
    assert strip_wake_phrase("hey friday") == ""
    assert strip_wake_phrase("what day is friday") == "what day is friday"


def test_speakable_removes_markdown_code_and_links() -> None:
    text = "**Done!**\n- made `a.txt`\n```py\nprint(1)\n```\nSee https://x.dev/y now."
    assert speakable(text) == "Done! made a.txt See a link now."


def test_long_replies_are_trimmed_at_a_sentence_boundary() -> None:
    long_text = "This is a sentence. " * 80
    out = speakable(long_text, limit=100)
    assert out.endswith("The full answer is on screen.")
    assert len(out) < 140 and "sentence." in out
