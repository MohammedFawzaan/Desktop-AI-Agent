"""Tools and the registry that holds them: what a tool is (`Tool`, `Risk`, `ToolResult`), how
tools are registered with `@registry.tool`, and how calls are approved, run and timed."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import typing
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from friday.agent.llm import CallInfo, ToolCall, ToolSpec

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
    calls: tuple[CallInfo, ...] = ()  # LLM calls made inside the tool
    calls_purpose: str = "tool"  # how those calls show up in the metrics, e.g. "vision"

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
    group: str = "pc"

    async def run(self, args: BaseModel) -> Any:
        if inspect.iscoroutinefunction(self.fn):
            result: Awaitable[Any] = self.fn(args)
            return await result
        return await asyncio.to_thread(self.fn, args)


@dataclass(frozen=True, slots=True)
class Decision:
    allow: bool
    reason: str = ""


class Approver(Protocol):
    async def __call__(self, tool: Tool, args: BaseModel) -> Decision: ...


async def default_approver(tool: Tool, args: BaseModel) -> Decision:
    if tool.risk is Risk.DESTRUCTIVE:
        return Decision(False, "destructive tools need explicit approval")
    return Decision(True)


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors()
    )


def _drop_args(fn: Callable[..., Any]) -> Callable[[Any], Any]:
    if inspect.iscoroutinefunction(fn):

        async def run_async(_args: Any) -> Any:
            return await fn()

        return run_async

    def run_sync(_args: Any) -> Any:
        return fn()

    return run_sync


class ToolRegistry:
    def __init__(self, approver: Approver = default_approver) -> None:
        self._tools: dict[str, Tool] = {}
        self._approver = approver

    def tool(
        self,
        *,
        risk: Risk = Risk.READ,
        name: str | None = None,
        timeout_s: float = 30.0,
        group: str = "pc",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            params = list(inspect.signature(fn).parameters)
            args_model: type[BaseModel] = EmptyArgs
            if params:
                hint = typing.get_type_hints(fn).get(params[0])
                if not (isinstance(hint, type) and issubclass(hint, BaseModel)):
                    raise TypeError(f"{fn.__name__}: first parameter must be a pydantic model")
                args_model = hint
            tool_name = name or fn.__name__
            if tool_name in self._tools:
                raise ValueError(f"Duplicate tool: {tool_name}")
            call_fn = fn if params else _drop_args(fn)

            self._tools[tool_name] = Tool(
                name=tool_name,
                description=inspect.getdoc(fn) or tool_name,
                args_model=args_model,
                fn=call_fn,
                risk=risk,
                timeout_s=timeout_s,
                group=group,
            )
            return fn

        return decorator

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def groups(self) -> list[str]:
        return sorted({t.group for t in self._tools.values()})

    def specs(
        self, *, read_only: bool = False, groups: Collection[str] | None = None
    ) -> list[ToolSpec]:
        return [
            ToolSpec(t.name, t.description, t.args_model.model_json_schema())
            for t in self._tools.values()
            if (not read_only or t.risk is Risk.READ) and (groups is None or t.group in groups)
        ]

    def describe(self) -> str:
        """Compact catalog for the planner: one line per tool, grouped."""
        lines: list[str] = []
        for group in self.groups():
            names = ", ".join(t.name for t in self._tools.values() if t.group == group)
            lines.append(f"- {group}: {names}")
        return "\n".join(lines)

    async def execute(self, call: ToolCall, *, read_only: bool = False) -> ToolResult:
        started = time.perf_counter()
        result = await self._execute(call, read_only=read_only)
        return replace(result, duration_s=time.perf_counter() - started)

    async def _execute(self, call: ToolCall, *, read_only: bool) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            available = ", ".join(sorted(self._tools)) or "none"
            return ToolResult.fail(f"Unknown tool '{call.name}'. Available: {available}")
        if read_only and tool.risk is not Risk.READ:
            return ToolResult.fail(f"'{call.name}' is not allowed here (read-only tools only).")
        try:
            args = tool.args_model.model_validate(call.args)
        except ValidationError as exc:
            return ToolResult.fail(f"Invalid arguments: {_format_validation_error(exc)}")
        decision = await self._approver(tool, args)
        if not decision.allow:
            return ToolResult.fail(f"Blocked: {decision.reason or 'not approved'}")
        try:
            async with asyncio.timeout(tool.timeout_s):
                value = await tool.run(args)
                return value if isinstance(value, ToolResult) else ToolResult.success(value)
        except TimeoutError:
            return ToolResult.fail(f"Timed out after {tool.timeout_s:g}s.")
        except Exception as exc:
            return ToolResult.fail(f"{type(exc).__name__}: {exc}")

    async def execute_all(self, calls: tuple[ToolCall, ...]) -> list[ToolResult]:
        # Desktop actions depend on each other, so run in order and stop at the first failure
        # of a state-changing tool. Pure reads are safe to run concurrently.
        if all((t := self._tools.get(c.name)) is not None and t.risk is Risk.READ for c in calls):
            return list(await asyncio.gather(*(self.execute(c) for c in calls)))
        results: list[ToolResult] = []
        for call in calls:
            if results and not results[-1].ok:
                results.append(ToolResult.fail("Skipped: an earlier action in this turn failed."))
                continue
            results.append(await self.execute(call))
        return results
