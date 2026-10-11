from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from typing import Any

from google.genai import types
from pydantic import BaseModel

from friday.agent.llm import (
    CallInfo,
    LLMResponse,
    T,
    Thinking,
    ToolCall,
    ToolSpec,
    Usage,
)
from friday.agent.loop import Plan, Step, Verdict
from friday.tools.registry import Decision

INFO = CallInfo(Usage(10, 5, 0), "fake-model", 0.01)


def call(name: str, **args: Any) -> ToolCall:
    return ToolCall(name=name, args=args)


def act_turn(*calls: ToolCall) -> LLMResponse:
    parts = [types.Part(function_call=types.FunctionCall(name=c.name, args=c.args)) for c in calls]
    content = types.Content(role="model", parts=parts)
    return LLMResponse(content, "", tuple(calls), INFO)


def say(text: str) -> LLMResponse:
    content = types.Content(role="model", parts=[types.Part(text=text)])
    return LLMResponse(content, text, (), INFO)


def submit(status: str = "passed", summary: str = "") -> LLMResponse:
    return act_turn(
        call("submit_verdict", status=status, reason="ok", evidence="tool output", summary=summary)
    )


def answer(text: str) -> Plan:
    return Plan(direct_answer=text)


def plan(*descriptions: str) -> Plan:
    return Plan(
        steps=[Step(description=d, expected_outcome=f"{d} is done") for d in descriptions],
        success_criteria="everything is done",
    )


def verdict(status: str = "passed", reason: str = "ok", summary: str = "") -> Verdict:
    return Verdict(
        status=status,  # type: ignore[arg-type]
        reason=reason,
        evidence="tool output shows it",
        summary=summary,
    )


class FakeLLM:
    """Scripted LLM: `generate` replays a queue; structured calls are queued per schema type."""

    def __init__(self) -> None:
        self.turns: deque[LLMResponse] = deque()
        self.structured: dict[type[BaseModel], deque[BaseModel]] = defaultdict(deque)
        self.generate_calls = 0
        self.seen: list[str] = []
        self.tool_names: list[list[str]] = []
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
    ) -> LLMResponse:
        self.generate_calls += 1
        self.tool_names.append([t.name for t in tools])
        if not self.turns:
            raise AssertionError("FakeLLM: no scripted act turn left")
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
        first = contents[0].parts[0].text if contents and contents[0].parts else ""
        self.seen.append(first or "")
        queue = self.structured[schema]
        if not queue:
            raise AssertionError(f"FakeLLM: no scripted {schema.__name__} left")
        item = queue.popleft()
        assert isinstance(item, schema)
        return item, INFO


async def allow_all(tool: Any, args: Any) -> Decision:
    """An approver that allows every tool call (for testing tools directly)."""
    return Decision(True)
