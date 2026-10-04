"""Tools that act on the user's real PC: files and PowerShell."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from send2trash import send2trash

from desktop_ai_agent.env import system_context
from desktop_ai_agent.tools.base import Risk, ToolResult
from desktop_ai_agent.tools.registry import Approver, ToolRegistry, default_approver

_STREAM_LIMIT = 6000
_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "$Recycle.Bin",
    "System Volume Information",
}


def resolve_path(raw: str) -> Path:
    """Expand ~ and %VARS%; relative paths are taken relative to the user's home folder."""
    expanded = os.path.expandvars(os.path.expanduser(raw.strip().strip('"')))
    path = Path(expanded)
    if not path.is_absolute():
        path = Path.home() / path
    return path.resolve(strict=False)


class PathArgs(BaseModel):
    path: str = Field(description="Absolute path (or ~/relative to the home folder).")


class ReadArgs(BaseModel):
    path: str = Field(description="Absolute path of the text file to read.")
    max_chars: int = Field(default=20000, ge=1, le=100000)


class WriteArgs(BaseModel):
    path: str = Field(description="Absolute path of the file. Parent folders are created.")
    content: str = Field(description="Full text content.")
    mode: Literal["create", "overwrite", "append"] = Field(
        default="create",
        description="create fails if the file exists; overwrite replaces it; append adds to it.",
    )


class ListArgs(BaseModel):
    path: str = Field(description="Absolute path of the folder.")
    limit: int = Field(default=200, ge=1, le=1000)


class FindArgs(BaseModel):
    root: str = Field(description="Absolute folder to search under.")
    pattern: str = Field(default="*", description="Case-insensitive glob on the name, e.g. *.pdf")
    kind: Literal["any", "file", "dir"] = "any"
    max_results: int = Field(default=50, ge=1, le=500)
    max_depth: int = Field(default=6, ge=0, le=12)


class CommandArgs(BaseModel):
    command: str = Field(description="PowerShell command or script to run (non-interactive).")
    cwd: str | None = Field(
        default=None, description="Working folder. Defaults to the home folder."
    )
    timeout_s: int = Field(default=60, ge=1, le=600)


def _clip(text: str) -> str:
    if len(text) <= _STREAM_LIMIT:
        return text
    half = _STREAM_LIMIT // 2
    return f"{text[:half]}\n...[{len(text) - _STREAM_LIMIT} chars omitted]...\n{text[-half:]}"


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    killer = await asyncio.create_subprocess_exec(
        "taskkill",
        "/PID",
        str(proc.pid),
        "/T",
        "/F",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await killer.wait()
    await proc.wait()


async def run_powershell(command: str, cwd: Path, timeout_s: int) -> ToolResult:
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$ProgressPreference='SilentlyContinue'; " + command
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    proc = await asyncio.create_subprocess_exec(
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-OutputFormat",
        "Text",
        "-EncodedCommand",
        encoded,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        await _kill_tree(proc)
        return ToolResult.fail(f"Command timed out after {timeout_s}s and was killed.")
    except BaseException:
        await _kill_tree(proc)
        raise
    text = (
        f"exit_code: {proc.returncode}\n"
        f"stdout:\n{_clip(out.decode('utf-8', errors='replace').strip())}\n"
        f"stderr:\n{_clip(err.decode('utf-8', errors='replace').strip())}"
    )
    return ToolResult.success(text) if proc.returncode == 0 else ToolResult.fail(text)


def build_pc_registry(approver: Approver = default_approver) -> ToolRegistry:
    registry = ToolRegistry(approver)

    @registry.tool(risk=Risk.READ, name="get_system_context")
    def get_system_context() -> str:
        """Discover facts about this PC: OS, hardware, user, drives, current time, and the real
        locations of Desktop, Documents, Downloads, Pictures, Music and Videos. Call this before
        using any path or system detail you do not already know; never guess them."""
        return system_context()

    @registry.tool(risk=Risk.READ)
    def read_file(args: ReadArgs) -> str:
        """Read a UTF-8 text file from the PC."""
        path = resolve_path(args.path)
        if not path.is_file():
            raise FileNotFoundError(f"Not a file: {path}")
        with path.open("rb") as fh:
            head = fh.read(4096)
        if b"\x00" in head:
            raise ValueError(f"{path} looks like a binary file.")
        with path.open(encoding="utf-8", errors="replace") as fh:
            text = fh.read(args.max_chars + 1)
        if len(text) > args.max_chars:
            return text[: args.max_chars] + f"\n...[truncated at {args.max_chars} chars]"
        return text

    @registry.tool(risk=Risk.READ)
    def list_dir(args: ListArgs) -> str:
        """List a folder: type, size, modified time and name."""
        path = resolve_path(args.path)
        if not path.is_dir():
            raise NotADirectoryError(f"Not a folder: {path}")
        lines: list[str] = []
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for entry in entries[: args.limit]:
            try:
                st = entry.stat()
            except OSError:
                lines.append(f"?    {entry.name}")
                continue
            kind = "dir " if entry.is_dir() else "file"
            size = "" if entry.is_dir() else str(st.st_size)
            when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{kind} {size:>12} {when} {entry.name}")
        if len(entries) > args.limit:
            lines.append(f"...[{len(entries) - args.limit} more entries not shown]")
        return "\n".join(lines) or "(empty folder)"

    @registry.tool(risk=Risk.READ, timeout_s=60)
    def find_paths(args: FindArgs) -> str:
        """Search for files or folders by name pattern under a folder."""
        root = resolve_path(args.root)
        if not root.is_dir():
            raise NotADirectoryError(f"Not a folder: {root}")
        pattern = args.pattern.lower()
        found: list[str] = []
        base_depth = len(root.parts)
        for current, dirs, files in os.walk(root):
            depth = len(Path(current).parts) - base_depth
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS] if depth < args.max_depth else []
            names = [(d, True) for d in dirs] + [(f, False) for f in files]
            for name, is_dir in names:
                if (args.kind == "file" and is_dir) or (args.kind == "dir" and not is_dir):
                    continue
                if fnmatch.fnmatch(name.lower(), pattern):
                    found.append(str(Path(current) / name))
                    if len(found) >= args.max_results:
                        return "\n".join(found) + f"\n...[stopped at {args.max_results} results]"
        return "\n".join(found) or "(no matches)"

    @registry.tool(risk=Risk.WRITE)
    def write_file(args: WriteArgs) -> str:
        """Create, overwrite or append to a UTF-8 text file on the PC."""
        path = resolve_path(args.path)
        if args.mode == "create" and path.exists():
            raise FileExistsError(f"{path} already exists. Use mode='overwrite' or 'append'.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a" if args.mode == "append" else "w", encoding="utf-8", newline="") as fh:
            fh.write(args.content)
        return f"Wrote {len(args.content)} chars ({path.stat().st_size} bytes total) to {path}"

    @registry.tool(risk=Risk.DESTRUCTIVE)
    def delete_path(args: PathArgs) -> str:
        """Move a file or folder to the Recycle Bin (recoverable)."""
        path = resolve_path(args.path)
        if not path.exists():
            raise FileNotFoundError(f"Does not exist: {path}")
        send2trash(str(path))
        return f"Moved to Recycle Bin: {path}"

    @registry.tool(risk=Risk.WRITE, timeout_s=640)
    async def run_command(args: CommandArgs) -> ToolResult:
        """Run a non-interactive Windows PowerShell command; returns exit code, stdout, stderr."""
        cwd = resolve_path(args.cwd) if args.cwd else Path.home()
        if not cwd.is_dir():
            return ToolResult.fail(f"Working folder does not exist: {cwd}")
        return await run_powershell(args.command, cwd, args.timeout_s)

    return registry
