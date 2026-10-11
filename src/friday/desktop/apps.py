"""Find and launch installed apps by name, using the same list the Start menu uses."""

from __future__ import annotations

import difflib
import json
import subprocess
from dataclasses import dataclass
from typing import Any

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


@dataclass(frozen=True, slots=True)
class StartApp:
    name: str
    app_id: str


def load_start_apps() -> list[StartApp]:
    """Every app in the Start menu (classic and Store apps), with the id that launches it."""
    out = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
            "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        timeout=30,
        creationflags=_NO_WINDOW,
        check=False,
    )
    try:
        raw: Any = json.loads(out.stdout.decode("utf-8", errors="replace") or "[]")
    except json.JSONDecodeError:
        return []
    rows: list[Any] = list(raw) if isinstance(raw, list) else [raw]  # pyright: ignore[reportUnknownArgumentType]
    apps: list[StartApp] = []
    for row in rows:
        if isinstance(row, dict) and row.get("Name") and row.get("AppID"):  # pyright: ignore[reportUnknownMemberType]
            apps.append(StartApp(str(row["Name"]), str(row["AppID"])))  # pyright: ignore[reportUnknownArgumentType]
    return apps


def match_app(query: str, apps: list[StartApp]) -> StartApp | None:
    """Best match for a spoken or typed app name: exact, then prefix, then contains, then fuzzy."""
    needle = query.lower().strip().removesuffix(".exe")
    if not needle:
        return None
    by_name = {a.name.lower(): a for a in apps}
    if needle in by_name:
        return by_name[needle]
    for test in (str.startswith, str.__contains__):
        hits = [a for a in apps if test(a.name.lower(), needle)]
        if hits:
            return min(hits, key=lambda a: len(a.name))  # "Paint" over "Paint 3D Preview"
    close = difflib.get_close_matches(needle, list(by_name), n=1, cutoff=0.6)
    return by_name[close[0]] if close else None


def suggestions(query: str, apps: list[StartApp], n: int = 6) -> list[str]:
    names = [a.name for a in apps]
    return difflib.get_close_matches(query, names, n=n, cutoff=0.3)


def launch(app: StartApp) -> None:
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app.app_id}"], creationflags=_NO_WINDOW)
