from __future__ import annotations

import asyncio

from pydantic import BaseModel

from friday.tools import Risk, ToolRegistry
from friday.tools.registry import MAX_OUTPUT_CHARS
from tests.fakes import call


class AddArgs(BaseModel):
    a: int
    b: int


def make_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(risk=Risk.READ)
    def add(args: AddArgs) -> int:
        """Add two numbers."""
        return args.a + args.b

    @reg.tool(risk=Risk.READ)
    async def ping() -> str:
        """Async, no arguments."""
        return "pong"

    @reg.tool(risk=Risk.WRITE)
    def boom() -> str:
        """Always fails."""
        raise RuntimeError("kaput")

    @reg.tool(risk=Risk.WRITE, timeout_s=0.05)
    async def slow() -> str:
        """Too slow."""
        await asyncio.sleep(1)
        return "never"

    @reg.tool(risk=Risk.DESTRUCTIVE)
    def wipe() -> str:
        """Dangerous."""
        return "wiped"

    @reg.tool(risk=Risk.READ)
    def huge() -> str:
        """Big output."""
        return "x" * (MAX_OUTPUT_CHARS * 2)

    return reg


async def test_executes_sync_and_async_tools() -> None:
    reg = make_registry()
    assert (await reg.execute(call("add", a=2, b=3))).output == "5"
    assert (await reg.execute(call("ping"))).output == "pong"


async def test_invalid_args_and_unknown_tool_give_actionable_errors() -> None:
    reg = make_registry()
    bad = await reg.execute(call("add", a="x"))
    assert not bad.ok and "a:" in bad.error and "b:" in bad.error
    unknown = await reg.execute(call("nope"))
    assert not unknown.ok and "Available:" in unknown.error


async def test_exceptions_timeouts_and_policy_become_failed_results() -> None:
    reg = make_registry()
    assert "RuntimeError: kaput" in (await reg.execute(call("boom"))).error
    assert "Timed out" in (await reg.execute(call("slow"))).error
    assert "Blocked:" in (await reg.execute(call("wipe"))).error


async def test_read_only_mode_rejects_state_changing_tools() -> None:
    reg = make_registry()
    assert not (await reg.execute(call("boom"), read_only=True)).ok
    assert (await reg.execute(call("ping"), read_only=True)).ok
    assert {s.name for s in reg.specs(read_only=True)} == {"add", "ping", "huge"}


async def test_output_is_truncated() -> None:
    result = await make_registry().execute(call("huge"))
    assert len(result.output) < MAX_OUTPUT_CHARS + 100 and "truncated" in result.output


async def test_failure_skips_remaining_calls_in_turn() -> None:
    reg = make_registry()
    results = await reg.execute_all((call("boom"), call("ping")))
    assert [r.ok for r in results] == [False, False]
    assert "Skipped" in results[1].error
