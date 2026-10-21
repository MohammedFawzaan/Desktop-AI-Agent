"""The model interface the agent depends on, and the data that goes in and out of it.

`GeminiLLM` (gemini.py) implements it; tests use a fake.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from google.genai import types
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Thinking(StrEnum):
    LOW = "low"
    HIGH = "high"


class LLMError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema of the arguments


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.output_tokens + other.output_tokens,
            self.thought_tokens + other.thought_tokens,
        )


@dataclass(frozen=True, slots=True)
class CallInfo:
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    latency_s: float = 0.0


@dataclass(frozen=True, slots=True)
class Source:
    """A web page that a search-grounded answer was based on."""

    title: str
    url: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    # The raw model turn. Append it to the history as is: it carries the thought signatures
    # Gemini needs to keep its reasoning intact across tool calls.
    content: types.Content
    text: str
    tool_calls: tuple[ToolCall, ...]
    info: CallInfo
    sources: tuple[Source, ...] = ()  # set when the answer used Google Search


class LLM(Protocol):
    async def generate(
        self,
        *,
        system: str,
        contents: Sequence[types.Content],
        tools: Sequence[ToolSpec] = (),
        thinking: Thinking = Thinking.LOW,
        web_search: bool = False,
    ) -> LLMResponse: ...

    async def generate_structured(
        self,
        *,
        system: str,
        contents: Sequence[types.Content],
        schema: type[T],
        thinking: Thinking = Thinking.LOW,
    ) -> tuple[T, CallInfo]: ...


def user_text(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def tool_results_content(
    calls: Sequence[ToolCall], payloads: Sequence[dict[str, Any]]
) -> types.Content:
    """Gemini needs one function_response per function_call, in the same order, in one turn."""
    parts = [
        types.Part(
            function_response=types.FunctionResponse(id=call.id, name=call.name, response=payload)
        )
        for call, payload in zip(calls, payloads, strict=True)
    ]
    return types.Content(role="user", parts=parts)
