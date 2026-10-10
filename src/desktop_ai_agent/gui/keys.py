"""Parse key descriptions like "ctrl+s" or "ctrl+a, delete" into virtual-key codes."""

from __future__ import annotations

MODIFIERS = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
NAMED = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "capslock": 0x14, "printscreen": 0x2C, "menu": 0x5D,
    "apps": 0x5D, "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "semicolon": 0xBA, "quote": 0xDE, "backslash": 0xDC,
    "volumeup": 0xAF, "volumedown": 0xAE, "mute": 0xAD, "playpause": 0xB3,
}  # fmt: skip
_PUNCTUATION = {"-": 0xBD, "=": 0xBB, ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE}
_NUMPAD = {
    "*": 0x6A,
    "multiply": 0x6A,
    "add": 0x6B,
    "subtract": 0x6D,
    "divide": 0x6F,
    "decimal": 0x6E,
}

Combo = tuple[tuple[int, ...], int]


class KeyParseError(ValueError):
    pass


def _vk(name: str) -> int:
    if name in NAMED:
        return NAMED[name]
    if name in _PUNCTUATION:
        return _PUNCTUATION[name]
    if name in _NUMPAD:
        return _NUMPAD[name]
    if name.startswith("num") and name[3:].isdigit() and len(name) == 4:
        return 0x60 + int(name[3])
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    raise KeyParseError(f"Unknown key '{name}'")


def parse_keys(spec: str) -> list[Combo]:
    """'ctrl+shift+s' -> one combo; 'ctrl+a, delete' -> two combos pressed in order."""
    combos: list[Combo] = []
    for chunk in spec.split(","):
        parts = [p.strip().lower() for p in chunk.split("+") if p.strip()]
        if not parts:
            continue
        *mods, key = parts
        if key in MODIFIERS and not mods:  # a lone modifier, e.g. "win"
            combos.append(((), MODIFIERS[key]))
            continue
        for mod in mods:
            if mod not in MODIFIERS:
                raise KeyParseError(f"'{mod}' is not a modifier (use ctrl, alt, shift or win)")
        combos.append((tuple(MODIFIERS[m] for m in mods), _vk(key)))
    if not combos:
        raise KeyParseError("No keys given")
    return combos


def normalize(spec: str) -> list[str]:
    """Canonical lowercase form of each combo, for safety checks: 'Alt + F4' -> ['alt+f4']."""
    return [
        "+".join(p.strip().lower() for p in chunk.split("+") if p.strip())
        for chunk in spec.split(",")
        if chunk.strip()
    ]
