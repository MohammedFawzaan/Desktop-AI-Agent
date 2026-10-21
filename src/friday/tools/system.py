"""System tool: facts about this PC (OS, hardware, user, real folder locations, time, country),
read fresh at runtime so the agent never has to guess them."""

from __future__ import annotations

import ctypes
import getpass
import os
import platform
import shutil
import socket
from ctypes import wintypes
from datetime import datetime
from functools import cache
from pathlib import Path

from friday.tools.registry import Risk, ToolRegistry

# Windows "known folder" identifiers (constants of the OS API, not user paths).
_FOLDER_IDS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Videos": "{18989B1D-99B5-42FD-8D13-AB6CB8AF3DE3}",
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _known_folder(folder_id: str) -> Path | None:
    windll = ctypes.windll
    guid = _GUID()
    if windll.ole32.CLSIDFromString(folder_id, ctypes.byref(guid)) != 0:
        return None
    raw = ctypes.c_void_p()
    if windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(raw)) != 0:
        return None
    try:
        return Path(ctypes.wstring_at(raw.value)) if raw.value else None
    finally:
        windll.ole32.CoTaskMemFree(raw)


@cache
def known_folders() -> dict[str, Path]:
    """Real locations of the user's folders (Desktop is often redirected into OneDrive)."""
    folders: dict[str, Path] = {}
    for name, folder_id in _FOLDER_IDS.items():
        folders[name] = _known_folder(folder_id) or (Path.home() / name)
    return folders


def _memory_gb() -> tuple[float, float] | None:
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    gb = 1024**3
    return status.ullTotalPhys / gb, status.ullAvailPhys / gb


def _drives() -> list[str]:
    lines: list[str] = []
    for root in os.listdrives():
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        gb = 1024**3
        lines.append(f"  {root}: {usage.free / gb:.0f} GB free of {usage.total / gb:.0f} GB")
    return lines


def _region() -> str:
    """The country set in Windows (Settings > Time & language > Region) and the format locale."""
    country = ctypes.create_unicode_buffer(16)
    locale = ctypes.create_unicode_buffer(85)
    kernel32 = ctypes.windll.kernel32
    has_country = kernel32.GetUserDefaultGeoName(country, len(country)) > 0
    has_locale = kernel32.GetUserDefaultLocaleName(locale, len(locale)) > 0
    return (
        f"{country.value if has_country else 'unknown'} "
        f"(regional format {locale.value if has_locale else 'unknown'})"
    )


def system_context() -> str:
    """Current facts about this PC, gathered fresh on every call."""
    folders = "\n".join(
        f"  {name}: {path}{'' if path.exists() else '  (does not exist)'}"
        for name, path in known_folders().items()
    )
    memory = _memory_gb()
    ram = f"{memory[0]:.1f} GB total, {memory[1]:.1f} GB available" if memory else "unknown"
    now = datetime.now().astimezone()
    lines = [
        f"OS: {platform.platform()} ({platform.machine()})",
        f"Computer name: {socket.gethostname()}",
        f"User: {getpass.getuser()}",
        f"Home folder: {Path.home()}",
        f"Current working folder: {Path.cwd()}",
        f"CPU: {platform.processor() or 'unknown'}, {os.cpu_count()} logical cores",
        f"RAM: {ram}",
        "Drives:",
        *_drives(),
        f"Known folders (real locations):\n{folders}",
        "Shell used by run_command: Windows PowerShell",
        f"Local time: {now.isoformat(timespec='seconds')} ({now.tzname()})",
        f"Country (Windows region setting): {_region()}",
    ]
    return "\n".join(lines)


def register_system_tools(registry: ToolRegistry) -> None:
    @registry.tool(risk=Risk.READ)
    def get_system_context() -> str:
        """Facts about this PC: OS, hardware, user, drives, local time and time zone, the user's
        country, and the real locations of Desktop, Documents, Downloads, Pictures, Music and
        Videos. Call it before using any path or system detail you do not know; never guess."""
        return system_context()
