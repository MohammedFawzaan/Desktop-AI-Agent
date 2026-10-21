from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from friday.tools.registry import MAX_OUTPUT_CHARS, Decision, Risk, ToolRegistry
from tests.fakes import allow_all, call


class AddArgs(BaseModel):
    a: int
    b: int


def make_registry(approver: Any = allow_all) -> ToolRegistry:
    tools = ToolRegistry(approver)

    @tools.tool()
    def add(args: AddArgs) -> int:
        """Add two numbers."""
        return args.a + args.b

    @tools.tool()
    async def ping() -> str:
        """Async, no arguments."""
        return "pong"

    @tools.tool(risk=Risk.WRITE)
    def boom() -> str:
        """Always fails."""
        raise RuntimeError("kaput")

    @tools.tool(risk=Risk.WRITE, timeout_s=0.05)
    async def slow() -> str:
        """Too slow."""
        await asyncio.sleep(1)
        return "never"

    @tools.tool()
    def huge() -> str:
        """Big output."""
        return "x" * (MAX_OUTPUT_CHARS * 2)

    return tools


async def test_runs_sync_and_async_tools() -> None:
    tools = make_registry()
    assert (await tools.execute(call("add", a=2, b=3))).output == "5"
    assert (await tools.execute(call("ping"))).output == "pong"


async def test_bad_arguments_and_unknown_tools_give_readable_errors() -> None:
    tools = make_registry()
    bad = await tools.execute(call("add", a="x"))
    assert not bad.ok and "a:" in bad.error and "b:" in bad.error
    assert "Unknown tool" in (await tools.execute(call("nope"))).error


async def test_exceptions_and_timeouts_become_failed_results() -> None:
    tools = make_registry()
    assert "RuntimeError: kaput" in (await tools.execute(call("boom"))).error
    assert "Timed out" in (await tools.execute(call("slow"))).error


async def test_the_approver_can_block_a_call() -> None:
    async def deny_writes(tool: Any, args: Any) -> Decision:
        return Decision(tool.risk is Risk.READ, "no writes today")

    tools = make_registry(deny_writes)
    assert (await tools.execute(call("ping"))).ok
    assert (await tools.execute(call("boom"))).error == "Blocked: no writes today"


async def test_long_output_is_cut() -> None:
    result = await make_registry().execute(call("huge"))
    assert len(result.output) < MAX_OUTPUT_CHARS + 100 and "[cut" in result.output


async def test_a_failure_skips_the_rest_of_the_turn() -> None:
    results = await make_registry().execute_all((call("boom"), call("ping")))
    assert [r.ok for r in results] == [False, False] and "Skipped" in results[1].error


def test_the_planner_catalog_is_one_line_per_group() -> None:
    tools = make_registry()

    @tools.tool(group="web")
    def read_page() -> str:
        """web tool"""
        return "page"

    assert tools.describe().splitlines() == [
        "- pc: add, ping, boom, slow, huge",
        "- web: read_page",
    ]
