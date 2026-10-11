"""Shell tool: run Windows PowerShell commands."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from pydantic import BaseModel, Field

from friday.tools.files import resolve_path
from friday.tools.registry import Risk, ToolRegistry, ToolResult

_STREAM_LIMIT = 6000


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


def register_shell_tools(registry: ToolRegistry) -> None:
    @registry.tool(risk=Risk.WRITE, timeout_s=640)
    async def run_command(args: CommandArgs) -> ToolResult:
        """Run a non-interactive Windows PowerShell command; returns exit code, stdout, stderr."""
        cwd = resolve_path(args.cwd) if args.cwd else Path.home()
        if not cwd.is_dir():
            return ToolResult.fail(f"Working folder does not exist: {cwd}")
        return await run_powershell(args.command, cwd, args.timeout_s)
