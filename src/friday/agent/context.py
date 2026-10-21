"""Everything one run needs: its state, and the only way to call the model or report progress.

Every model call goes through here, so the call budget, token usage and events are always kept
up to date, whichever phase (plan, act, verify) makes the call.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from google.genai import types

from friday.agent import prompts
from friday.agent.llm import LLM, CallInfo, LLMResponse, T, Thinking, ToolCall, ToolSpec
from friday.agent.models import Budget, RunResult, RunState, RunStatus
from friday.monitoring.events import Event, EventSink
from friday.monitoring.metrics import CallRecord
from friday.tools.registry import ToolResult


class BudgetExceeded(Exception):
    pass


class RunContext:
    def __init__(self, state: RunState, llm: LLM, budget: Budget, sink: EventSink) -> None:
        self.state = state
        self.budget = budget
        self._llm = llm
        self._sink = sink

    # ---- model calls ------------------------------------------------------------------------

    async def generate(
        self,
        *,
        purpose: str,
        system: str,
        contents: Sequence[types.Content],
        tools: Sequence[ToolSpec] = (),
        thinking: Thinking = Thinking.LOW,
        web_search: bool = False,
    ) -> LLMResponse:
        self._check_budget()
        resp = await self._llm.generate(
            system=system, contents=contents, tools=tools, thinking=thinking, web_search=web_search
        )
        self._record_call(purpose, resp.info)
        return resp

    async def structured(
        self,
        *,
        purpose: str,
        system: str,
        contents: Sequence[types.Content],
        schema: type[T],
        thinking: Thinking = Thinking.LOW,
    ) -> T:
        self._check_budget()
        parsed, info = await self._llm.generate_structured(
            system=system, contents=contents, schema=schema, thinking=thinking
        )
        self._record_call(purpose, info)
        return parsed

    def _check_budget(self) -> None:
        if self.state.llm_calls >= self.budget.max_llm_calls:
            raise BudgetExceeded(f"The limit of {self.budget.max_llm_calls} model calls was hit.")

    def _record_call(self, purpose: str, info: CallInfo) -> None:
        u = info.usage
        self.state.usage += u
        self.state.calls.append(
            CallRecord(
                purpose,
                info.model,
                u.prompt_tokens,
                u.output_tokens,
                u.thought_tokens,
                info.latency_s,
            )
        )

    # ---- progress ---------------------------------------------------------------------------

    def emit(self, kind: str, **data: Any) -> None:
        self._sink.emit(Event(kind, {"run_id": self.state.run_id, **data}))

    def record_tool(self, call: ToolCall, result: ToolResult) -> None:
        """Log a finished tool call (and any model calls it made itself, such as a web search)."""
        for info in result.calls:
            self._record_call(result.calls_purpose, info)
        self.state.tools_used.append(call.name)
        self.emit(
            "tool", tool=call.name, args=call.args, ok=result.ok, output=prompts.clip(result.text)
        )

    def result(self, status: RunStatus, summary: str) -> RunResult:
        state = self.state
        return RunResult(
            status=status,
            summary=summary,
            run_id=state.run_id,
            goal=state.goal,
            steps_completed=len(state.completed),
            usage=state.usage,
            elapsed_s=time.monotonic() - state.started,
            calls=tuple(state.calls),
        )
