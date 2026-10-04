from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from desktop_ai_agent.llm.base import ToolCall
from desktop_ai_agent.tools import Decision, ToolRegistry
from desktop_ai_agent.tools.pc import build_pc_registry


async def allow_all(tool: Any, args: Any) -> Decision:
    return Decision(True)


@pytest.fixture
def reg() -> ToolRegistry:
    return build_pc_registry(allow_all)


def call(name: str, **args: object) -> ToolCall:
    return ToolCall(name, dict(args))


async def test_write_read_append_and_overwrite_rules(reg: ToolRegistry, tmp_path: Path) -> None:
    target = str(tmp_path / "sub" / "note.txt")
    written = await reg.execute(call("write_file", path=target, content="hello"))
    assert written.ok and str(tmp_path) in written.output
    assert (await reg.execute(call("read_file", path=target))).output == "hello"

    again = await reg.execute(call("write_file", path=target, content="x"))
    assert not again.ok and "already exists" in again.error

    await reg.execute(call("write_file", path=target, content=" world", mode="append"))
    assert (await reg.execute(call("read_file", path=target))).output == "hello world"
    await reg.execute(call("write_file", path=target, content="new", mode="overwrite"))
    assert (await reg.execute(call("read_file", path=target))).output == "new"


async def test_read_rejects_missing_and_binary_files(reg: ToolRegistry, tmp_path: Path) -> None:
    assert not (await reg.execute(call("read_file", path=str(tmp_path / "nope.txt")))).ok
    binary = tmp_path / "b.bin"
    binary.write_bytes(b"\x00\x01\x02")
    result = await reg.execute(call("read_file", path=str(binary)))
    assert not result.ok and "binary" in result.error


async def test_list_dir_and_find_paths(reg: ToolRegistry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "deep" / "er").mkdir(parents=True)
    (tmp_path / "deep" / "er" / "b.TXT").write_text("2")
    listing = (await reg.execute(call("list_dir", path=str(tmp_path)))).output
    assert "a.txt" in listing and "deep" in listing
    found = (await reg.execute(call("find_paths", root=str(tmp_path), pattern="*.txt"))).output
    assert "a.txt" in found and "b.TXT" in found
    dirs = await reg.execute(call("find_paths", root=str(tmp_path), pattern="*", kind="dir"))
    assert "a.txt" not in dirs.output and "deep" in dirs.output


async def test_get_system_context_discovers_real_locations(reg: ToolRegistry) -> None:
    out = (await reg.execute(call("get_system_context"))).output
    for expected in ("OS:", "User:", "RAM:", "Drives:", "Desktop:", "Documents:", "Downloads:"):
        assert expected in out


async def test_run_command_success_failure_and_timeout(reg: ToolRegistry, tmp_path: Path) -> None:
    ok = await reg.execute(call("run_command", command="Write-Output 'héllo'", cwd=str(tmp_path)))
    assert ok.ok and "exit_code: 0" in ok.output and "héllo" in ok.output

    cwd = await reg.execute(call("run_command", command="(Get-Location).Path", cwd=str(tmp_path)))
    assert str(tmp_path) in cwd.output

    bad = await reg.execute(call("run_command", command="exit 3"))
    assert not bad.ok and "exit_code: 3" in bad.error

    err = await reg.execute(call("run_command", command="Get-Item C:\\definitely\\not\\here"))
    assert not err.ok and "stderr" in err.error

    slow = await reg.execute(call("run_command", command="Start-Sleep -Seconds 20", timeout_s=2))
    assert not slow.ok and "timed out" in slow.error
