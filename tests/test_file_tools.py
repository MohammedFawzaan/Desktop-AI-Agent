from __future__ import annotations

from pathlib import Path

import pytest

from friday.tools import ToolRegistry
from friday.tools.files import register_file_tools
from tests.fakes import allow_all, call


@pytest.fixture
def reg() -> ToolRegistry:
    registry = ToolRegistry(allow_all)
    register_file_tools(registry)
    return registry


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
