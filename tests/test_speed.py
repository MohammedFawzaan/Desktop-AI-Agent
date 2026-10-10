from __future__ import annotations

import asyncio
import time
from typing import Any

from google.genai import types

from desktop_ai_agent.agent import Agent, Budget
from desktop_ai_agent.agent.loop import trim_old_observations
from desktop_ai_agent.agent.state import Plan, Step
from desktop_ai_agent.llm import GeminiLLM, Thinking, ToolCall, tool_results_content, user_text
from desktop_ai_agent.tools import Risk, ToolRegistry
from tests.fakes import FakeLLM, act_turn, call, say, verdict


def grouped_tools() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(risk=Risk.WRITE)
    def write_note() -> str:
        """pc tool"""
        return "ok"

    @reg.tool(risk=Risk.WRITE, group="apps")
    def click_button() -> str:
        """apps tool"""
        return "ok"

    @reg.tool(risk=Risk.READ, group="web")
    def read_page() -> str:
        """web tool"""
        return "page"

    return reg


def step_plan(*groups: str) -> Plan:
    return Plan(
        steps=[Step(description="do it", expected_outcome="done", tools=list(groups))],
        success_criteria="done",
    )


async def test_each_step_only_gets_the_tool_groups_it_needs() -> None:
    llm = FakeLLM().queue(step_plan("web"), say("Read it."), verdict("passed", summary="ok"))
    await Agent(llm, grouped_tools(), budget=Budget(verify_probes=0)).run("x")
    assert sorted(llm.tool_names[0]) == ["read_page", "write_note"]  # pc is always included


async def test_a_retry_gets_every_tool_in_case_the_plan_picked_wrong() -> None:
    llm = FakeLLM().queue(
        step_plan("web"),
        say("Could not."),
        verdict("retry_step", "needs an app"),
        act_turn(call("click_button")),
        say("Clicked."),
        verdict("passed", summary="ok"),
    )
    result = await Agent(llm, grouped_tools(), budget=Budget(verify_probes=0)).run("x")
    assert result.status == "success"
    assert sorted(llm.tool_names[1]) == ["click_button", "read_page", "write_note"]


async def test_steps_without_groups_get_everything() -> None:
    llm = FakeLLM().queue(step_plan(), say("Done."), verdict("passed", summary="ok"))
    await Agent(llm, grouped_tools(), budget=Budget(verify_probes=0)).run("x")
    assert len(llm.tool_names[0]) == 3


def test_planner_catalog_is_compact_and_grouped() -> None:
    catalog = grouped_tools().describe()
    assert catalog.splitlines() == ["- apps: click_button", "- pc: write_note", "- web: read_page"]


def test_old_tool_outputs_are_trimmed_but_the_latest_stay_whole() -> None:
    contents = [user_text("goal")]
    for i in range(4):
        result = tool_results_content(
            [ToolCall("read_page", {})], [{"ok": True, "output": f"page {i} " + "x" * 2000}]
        )
        contents.append(result)
    trim_old_observations(contents, keep=2, limit=600)
    sizes: list[int] = []
    for content in contents[1:]:
        part = (content.parts or [])[0]
        assert part.function_response is not None and part.function_response.response is not None
        sizes.append(len(part.function_response.response["output"]))
    assert sizes[0] < 700 and sizes[1] < 700
    assert sizes[2] > 2000 and sizes[3] > 2000


class _SlowThenFast:
    """A fake Gemini endpoint whose first answer is stuck, like the 15 s spikes seen live."""

    def __init__(self) -> None:
        self.calls = 0
        self.aio = self
        self.models = self

    async def generate_content(self, **_: Any) -> types.GenerateContentResponse:
        self.calls += 1
        await asyncio.sleep(5 if self.calls == 1 else 0.01)
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(content=types.Content(role="model", parts=[types.Part(text="ok")]))
            ]
        )


async def test_a_stuck_request_is_raced_by_a_duplicate() -> None:
    llm = GeminiLLM("key", "model", hedge_after_s=0.05)
    fake = _SlowThenFast()
    llm._client = fake  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    started = time.monotonic()
    resp = await llm.generate(system="s", contents=[user_text("hi")], thinking=Thinking.LOW)
    assert resp.text == "ok" and time.monotonic() - started < 1.0
    assert fake.calls == 2 and llm.hedged == 1


async def test_long_thinking_calls_are_never_hedged() -> None:
    llm = GeminiLLM("key", "model", hedge_after_s=0.05)
    fake = _SlowThenFast()
    fake.calls = 1  # make the only call fast
    llm._client = fake  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    await llm.generate(system="s", contents=[user_text("hi")], thinking=Thinking.HIGH)
    assert llm.hedged == 0
