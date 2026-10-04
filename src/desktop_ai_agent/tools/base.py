from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

MAX_OUTPUT_CHARS = 8000


class Risk(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class EmptyArgs(BaseModel):
    pass


def render_output(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    if len(text) > MAX_OUTPUT_CHARS:
        cut = len(text) - MAX_OUTPUT_CHARS
        text = text[:MAX_OUTPUT_CHARS] + f"\n...[truncated {cut} chars]"
    return text


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""
    duration_s: float = 0.0

    @classmethod
    def success(cls, value: Any) -> ToolResult:
        return cls(ok=True, output=render_output(value))

    @classmethod
    def fail(cls, message: str) -> ToolResult:
        return cls(ok=False, error=message)

    def payload(self) -> dict[str, Any]:
        return (
            {"ok": True, "output": self.output} if self.ok else {"ok": False, "error": self.error}
        )

    @property
    def text(self) -> str:
        return self.output if self.ok else f"ERROR: {self.error}"


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[Any], Any]
    risk: Risk
    timeout_s: float

    async def run(self, args: BaseModel) -> Any:
        if inspect.iscoroutinefunction(self.fn):
            result: Awaitable[Any] = self.fn(args)
            return await result
        return await asyncio.to_thread(self.fn, args)
