from __future__ import annotations

from pathlib import Path

import pytest

from friday.tools import ToolRegistry
from friday.tools.shell import register_shell_tools
from tests.fakes import allow_all, call


@pytest.fixture
def reg() -> ToolRegistry:
    registry = ToolRegistry(allow_all)
    register_shell_tools(registry)
    return registry


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
