"""Test helpers: a scripted fake LLM, shortcuts to build its answers, and a tool registry."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from google.genai import types
from pydantic import BaseModel

from friday.agent.llm import CallInfo, LLMResponse, Source, T, Thinking, ToolCall, ToolSpec, Usage
from friday.agent.models import Plan, Step, Verdict
from friday.tools.registry import Decision, Risk, ToolRegistry

INFO = CallInfo(Usage(10, 5, 0), "fake-model", 0.01)


def call(name: str, **args: Any) -> ToolCall:
    return ToolCall(name=name, args=args)


def act_turn(*calls: ToolCall) -> LLMResponse:
    """The model asks for these tool calls."""
    parts = [types.Part(function_call=types.FunctionCall(name=c.name, args=c.args)) for c in calls]
    return LLMResponse(types.Content(role="model", parts=parts), "", tuple(calls), INFO)


def say(text: str) -> LLMResponse:
    """The model replies with plain text."""
    content = types.Content(role="model", parts=[types.Part(text=text)])
    return LLMResponse(content, text, (), INFO)


def found(text: str, *titles: str) -> LLMResponse:
    """A web-search answer, with one source per title."""
    sources = tuple(Source(t, f"https://{t.lower()}/page") for t in titles)
    return replace(say(text), sources=sources)


def answer(text: str) -> Plan:
    return Plan(direct_answer=text)


def plan(*descriptions: str) -> Plan:
    return Plan(
        steps=[Step(description=d, expected_outcome=f"{d} is done") for d in descriptions],
        success_criteria="everything is done",
    )


def verdict(status: str = "passed", reason: str = "ok", summary: str = "") -> Verdict:
    return Verdict.model_validate(
        {"status": status, "reason": reason, "evidence": "tool output", "summary": summary}
    )


class FakeLLM:
    """Replays scripted answers: `generate` from one queue, structured calls by schema type."""

    def __init__(self) -> None:
        self.turns: deque[LLMResponse] = deque()
        self.structured: dict[type[BaseModel], deque[BaseModel]] = defaultdict(deque)
        self.prompts: list[str] = []  # first message of every structured call
        self.tool_names: list[list[str]] = []  # tools offered on every generate call
        self.web_searches = 0
        self.structured_calls = 0

    def queue(self, *items: LLMResponse | BaseModel) -> FakeLLM:
        for item in items:
            if isinstance(item, LLMResponse):
                self.turns.append(item)
            else:
                self.structured[type(item)].append(item)
        return self

    async def generate(
        self,
        *,
        system: str,
        contents: Sequence[types.Content],
        tools: Sequence[ToolSpec] = (),
        thinking: Thinking = Thinking.LOW,
        web_search: bool = False,
    ) -> LLMResponse:
        self.tool_names.append([t.name for t in tools])
        self.web_searches += int(web_search)
        if not self.turns:
            raise AssertionError("FakeLLM: no scripted turn left")
        return self.turns.popleft()

    async def generate_structured(
        self,
        *,
        system: str,
        contents: Sequence[types.Content],
        schema: type[T],
        thinking: Thinking = Thinking.LOW,
    ) -> tuple[T, CallInfo]:
        self.structured_calls += 1
        parts = contents[0].parts if contents else None
        self.prompts.append((parts[0].text if parts else None) or "")
        queue = self.structured[schema]
        if not queue:
            raise AssertionError(f"FakeLLM: no scripted {schema.__name__} left")
        item = queue.popleft()
        assert isinstance(item, schema)
        return item, INFO


async def allow_all(tool: Any, args: Any) -> Decision:
    """An approver that allows every tool call."""
    return Decision(True)


def make_tools() -> ToolRegistry:
    """A small registry: one tool that changes something, one that reads, one that fails."""
    tools = ToolRegistry(allow_all)

    @tools.tool(risk=Risk.WRITE)
    def do_thing() -> str:
        """Change something."""
        return "changed"

    @tools.tool(risk=Risk.READ)
    def check_thing() -> str:
        """Read the state."""
        return "state=changed"

    @tools.tool(risk=Risk.WRITE)
    def broken() -> str:
        """Always fails."""
        raise RuntimeError("nope")

    return tools
