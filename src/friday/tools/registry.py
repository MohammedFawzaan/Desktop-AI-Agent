"""Tools and the registry that holds them.

A tool is a typed Python function registered with `@registry.tool`. The registry turns tools
into specs for the model, checks every call's arguments, asks the safety approver, and runs the
call with a timeout. Errors come back to the model as readable results, never as exceptions.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import typing
from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from friday.agent.llm import CallInfo, ToolCall, ToolSpec

MAX_OUTPUT_CHARS = 8000


class Risk(StrEnum):
    READ = "read"  # only looks at things
    WRITE = "write"  # changes something


class EmptyArgs(BaseModel):
    pass


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""
    calls: tuple[CallInfo, ...] = ()  # model calls made inside the tool (vision, web search)
    calls_purpose: str = "tool"  # how those calls are labelled in the metrics

    @classmethod
    def success(cls, value: Any) -> ToolResult:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + f"\n...[cut {len(text) - MAX_OUTPUT_CHARS} chars]"
        return cls(ok=True, output=text)

    @classmethod
    def fail(cls, message: str) -> ToolResult:
        return cls(ok=False, error=message)

    def payload(self) -> dict[str, Any]:
        """What the model sees."""
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
    fn: Callable[..., Any]
    takes_args: bool
    risk: Risk
    timeout_s: float
    group: str  # 'pc', 'apps' or 'web': the planner picks the groups each step needs

    async def run(self, args: BaseModel) -> Any:
        call_args = (args,) if self.takes_args else ()
        if inspect.iscoroutinefunction(self.fn):
            return await self.fn(*call_args)
        return await asyncio.to_thread(self.fn, *call_args)


@dataclass(frozen=True, slots=True)
class Decision:
    allow: bool
    reason: str = ""


class Approver(Protocol):
    """Decides whether a tool call may run (the safety policy)."""

    async def __call__(self, tool: Tool, args: BaseModel) -> Decision: ...


class ToolRegistry:
    def __init__(self, approver: Approver) -> None:
        self._tools: dict[str, Tool] = {}
        self._approver = approver

    def tool(
        self, *, risk: Risk = Risk.READ, timeout_s: float = 30.0, group: str = "pc"
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a function as a tool. Its docstring is the description the model reads and
        its single parameter (if any) is a pydantic model describing the arguments."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            params = list(inspect.signature(fn).parameters)
            args_model: type[BaseModel] = EmptyArgs
            if params:
                hint = typing.get_type_hints(fn).get(params[0])
                if not (isinstance(hint, type) and issubclass(hint, BaseModel)):
                    raise TypeError(f"{fn.__name__}: its parameter must be a pydantic model")
                args_model = hint
            if fn.__name__ in self._tools:
                raise ValueError(f"Duplicate tool: {fn.__name__}")
            self._tools[fn.__name__] = Tool(
                name=fn.__name__,
                description=inspect.getdoc(fn) or fn.__name__,
                args_model=args_model,
                fn=fn,
                takes_args=bool(params),
                risk=risk,
                timeout_s=timeout_s,
                group=group,
            )
            return fn

        return decorator

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def groups(self) -> list[str]:
        return sorted({t.group for t in self._tools.values()})

    def specs(self, *, groups: Collection[str] | None = None) -> list[ToolSpec]:
        return [
            ToolSpec(t.name, t.description, t.args_model.model_json_schema())
            for t in self._tools.values()
            if groups is None or t.group in groups
        ]

    def describe(self) -> str:
        """A short catalog for the planner: one line per group."""
        return "\n".join(
            f"- {group}: " + ", ".join(t.name for t in self._tools.values() if t.group == group)
            for group in self.groups()
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult.fail(
                f"Unknown tool '{call.name}'. Available: {', '.join(self._tools)}"
            )
        try:
            args = tool.args_model.model_validate(call.args)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in exc.errors()
            )
            return ToolResult.fail(f"Invalid arguments: {problems}")
        decision = await self._approver(tool, args)
        if not decision.allow:
            return ToolResult.fail(f"Blocked: {decision.reason or 'not approved'}")
        try:
            async with asyncio.timeout(tool.timeout_s):
                value = await tool.run(args)
        except TimeoutError:
            return ToolResult.fail(f"Timed out after {tool.timeout_s:g}s.")
        except Exception as exc:
            return ToolResult.fail(f"{type(exc).__name__}: {exc}")
        return value if isinstance(value, ToolResult) else ToolResult.success(value)

    async def execute_all(self, calls: Collection[ToolCall]) -> list[ToolResult]:
        """Reads run together. Anything that changes state runs in order, and the rest of the
        turn is skipped after a failure, because later actions usually depend on earlier ones."""
        if all((t := self._tools.get(c.name)) is not None and t.risk is Risk.READ for c in calls):
            return list(await asyncio.gather(*(self.execute(c) for c in calls)))
        results: list[ToolResult] = []
        for call in calls:
            if results and not results[-1].ok:
                results.append(ToolResult.fail("Skipped: an earlier action in this turn failed."))
            else:
                results.append(await self.execute(call))
        return results
