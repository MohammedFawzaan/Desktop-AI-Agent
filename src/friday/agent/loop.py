"""The agent loop: Plan -> Act -> Observe -> Verify -> Recover, until the goal is verified.

A run ends only on a verified result, a failure recovery cannot fix, or a used-up budget. The
model going quiet is never taken as success.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import asdict

from google.genai import types

from friday.agent import prompts
from friday.agent.context import BudgetExceeded, RunContext
from friday.agent.llm import LLM, LLMError, tool_results_content, user_text
from friday.agent.models import (
    ActionRecord,
    Budget,
    Phase,
    RunResult,
    RunState,
    SessionTurn,
    StepContext,
    StepRecord,
    Verdict,
)
from friday.agent.planner import answer_question, make_plan
from friday.agent.verifier import verify_step
from friday.monitoring.events import EventSink, NullSink
from friday.monitoring.metrics import estimate_cost
from friday.tools.registry import ToolRegistry

_KEEP_FULL_RESULTS = 2  # older tool results in a step are shortened to save tokens
_TRIMMED_RESULT_CHARS = 600


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: ToolRegistry,
        *,
        budget: Budget | None = None,
        sink: EventSink | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._budget = budget or Budget()
        self._sink = sink or NullSink()

    async def run(
        self, goal: str, *, session: Sequence[SessionTurn] = (), voice: bool = False
    ) -> RunResult:
        state = RunState(goal, uuid.uuid4().hex[:8], voice=voice, session=tuple(session))
        run = RunContext(state, self._llm, self._budget, self._sink)
        run.emit("run_started", goal=goal)
        try:
            async with asyncio.timeout(self._budget.timeout_s):
                result = await self._drive(run)
        except TimeoutError:
            result = run.result("budget_exceeded", "The run took too long and was stopped.")
        except BudgetExceeded as exc:
            result = run.result("budget_exceeded", str(exc))
        except LLMError as exc:
            result = run.result("failed", f"Model error: {exc}")
        run.emit(
            "run_finished",
            status=result.status,
            summary=result.summary,
            elapsed_s=round(result.elapsed_s, 2),
            llm_calls=result.llm_calls,
            usage=asdict(result.usage),
            cost_usd=estimate_cost(result.calls),
        )
        return result

    async def _drive(self, run: RunContext) -> RunResult:
        state = run.state
        phase = Phase.PLAN
        step: StepContext | None = None
        verdict: Verdict | None = None
        answer = ""

        while True:
            if phase is not Phase.PLAN:
                run.emit("phase", phase=phase.value)
            match phase:
                case Phase.PLAN:
                    run.emit("thinking")
                    plan = await make_plan(run, self._tools)
                    if not plan.steps:
                        answer, phase = await answer_question(run, plan), Phase.FINISH
                        continue
                    step, phase = self._new_step(state), Phase.ACT

                case Phase.ACT:
                    assert step is not None
                    phase = await self._act(run, step)

                case Phase.OBSERVE:
                    assert step is not None
                    phase = await self._observe(run, step)

                case Phase.VERIFY:
                    assert step is not None
                    verdict = await verify_step(run, self._tools, step)
                    if verdict.status != "passed":
                        phase = Phase.RECOVER
                        continue
                    state.completed.append(
                        StepRecord(state.current_step.description, verdict.evidence)
                    )
                    state.step_index += 1
                    state.step_attempts = 0
                    if state.has_more_steps:
                        step, phase = self._new_step(state), Phase.ACT
                    else:
                        answer, phase = verdict.summary or verdict.reason, Phase.FINISH

                case Phase.RECOVER:
                    assert verdict is not None
                    next_phase = self._recover(run, verdict)
                    if isinstance(next_phase, RunResult):
                        return next_phase
                    phase = next_phase
                    if phase is Phase.ACT:
                        step = self._new_step(state)

                case Phase.FINISH:
                    return run.result("success", answer)

    # ---- Act / Observe ----------------------------------------------------------------------

    def _new_step(self, state: RunState) -> StepContext:
        return StepContext(contents=[user_text(prompts.step_prompt(state))])

    async def _act(self, run: RunContext, step: StepContext) -> Phase:
        """Ask the model for the next move: tool calls to run, or a reply that the step is done."""
        if step.turns >= self._budget.max_turns_per_step:
            step.out_of_turns = True
            return Phase.VERIFY
        step.turns += 1
        resp = await run.generate(
            purpose="act",
            system=prompts.ACT_SYSTEM,
            contents=step.contents,
            tools=self._tools.specs(groups=self._tool_groups(run.state)),
        )
        step.contents.append(resp.content)
        if not resp.tool_calls:
            step.claim = resp.text
            return Phase.VERIFY
        step.pending = resp.tool_calls
        return Phase.OBSERVE

    def _tool_groups(self, state: RunState) -> set[str] | None:
        """The tool groups the planner chose for this step, or every tool on a retry."""
        if state.step_attempts:
            return None
        wanted = {g.strip().lower() for g in state.current_step.tools} & set(self._tools.groups())
        return (wanted | {"pc"}) if wanted else None

    async def _observe(self, run: RunContext, step: StepContext) -> Phase:
        """Run the requested tools and hand their results back to the model."""
        calls = step.pending
        results = await self._tools.execute_all(calls)
        for call, result in zip(calls, results, strict=True):
            step.actions.append(ActionRecord(call.name, call.args, result.ok, result.text))
            run.record_tool(call, result)
        step.contents.append(tool_results_content(calls, [r.payload() for r in results]))
        _shorten_old_results(step.contents)
        step.pending = ()
        return Phase.ACT

    # ---- Recover ----------------------------------------------------------------------------

    def _recover(self, run: RunContext, verdict: Verdict) -> Phase | RunResult:
        """After a failed check: retry the step, make a new plan, or stop."""
        state = run.state
        state.failures.append(f"Step '{state.current_step.description}': {verdict.reason}")
        if verdict.status == "blocked":
            run.emit("recover", decision="blocked")
            return run.result("blocked", verdict.reason)
        if verdict.status == "retry_step" and state.step_attempts < self._budget.max_step_retries:
            state.step_attempts += 1
            run.emit("recover", decision="retry step")
            return Phase.ACT
        if state.replans < self._budget.max_replans:
            state.replans += 1
            run.emit("recover", decision="replan")
            return Phase.PLAN
        run.emit("recover", decision="give up")
        return run.result("failed", f"Gave up after {state.replans} new plans: {verdict.reason}")


def _shorten_old_results(contents: list[types.Content]) -> None:
    """Shorten all but the latest tool results in a step, so each turn resends less."""
    tool_turns = [c for c in contents if c.parts and any(p.function_response for p in c.parts)]
    for content in tool_turns[:-_KEEP_FULL_RESULTS]:
        for part in content.parts or []:
            response = part.function_response.response if part.function_response else None
            for key in ("output", "error"):
                value = response.get(key) if response else None
                if response and isinstance(value, str) and len(value) > _TRIMMED_RESULT_CHARS:
                    response[key] = value[:_TRIMMED_RESULT_CHARS] + " ...[shortened]"
