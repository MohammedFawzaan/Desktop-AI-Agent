from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from typing import Any

from google.genai import types
from pydantic import ValidationError

from desktop_ai_agent.agent import prompts
from desktop_ai_agent.agent.state import (
    Budget,
    Phase,
    Plan,
    RunResult,
    RunState,
    RunStatus,
    SessionTurn,
    StepContext,
    StepRecord,
    Verdict,
)
from desktop_ai_agent.llm.base import (
    LLM,
    CallInfo,
    LLMError,
    LLMResponse,
    T,
    Thinking,
    ToolSpec,
    tool_results_content,
    user_text,
)
from desktop_ai_agent.obs.events import Event, EventSink, NullSink
from desktop_ai_agent.obs.metrics import CallRecord, ToolRecord, VerdictRecord
from desktop_ai_agent.tools.base import Risk
from desktop_ai_agent.tools.registry import ToolRegistry

SUBMIT_VERDICT = "submit_verdict"
_VERDICT_TOOL = ToolSpec(
    SUBMIT_VERDICT,
    "Submit your final verdict. Call this once you have enough evidence.",
    Verdict.model_json_schema(),
)


class BudgetExceeded(Exception):
    pass


class Agent:
    """Plan -> Act -> Observe -> Verify -> Recover -> Continue/Finish state machine.

    A run ends only on a verified goal, a blocked/failed verdict after recovery is exhausted,
    or an exhausted budget. The model going quiet is never treated as success.
    """

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
        state = RunState(
            goal=goal,
            run_id=uuid.uuid4().hex[:8],
            session=tuple(session),
            voice=voice,
        )
        self._emit(state, "run_started", goal=goal)
        try:
            async with asyncio.timeout(self._budget.timeout_s):
                result = await self._drive(state)
        except TimeoutError:
            result = self._result(state, "budget_exceeded", "Run timed out.")
        except BudgetExceeded as exc:
            result = self._result(state, "budget_exceeded", str(exc))
        except LLMError as exc:
            result = self._result(state, "failed", f"LLM error: {exc}")
        self._emit(state, "run_finished", status=result.status, summary=result.summary)
        return result

    async def _drive(self, state: RunState) -> RunResult:
        phase = Phase.PLAN
        ctx: StepContext | None = None
        verdict: Verdict | None = None

        while True:
            if phase is not Phase.PLAN:  # planning is announced only once we know it is a task
                self._emit(state, "phase", phase=phase.value)
            match phase:
                case Phase.PLAN:
                    if state.replans:
                        self._emit(state, "phase", phase="plan")
                    else:
                        self._emit(state, "thinking")
                    await self._plan(state)
                    plan = state.require_plan()
                    if not plan.steps:
                        state.summary = plan.direct_answer.strip()
                        phase = Phase.FINISH
                        continue
                    ctx = self._begin_step(state)
                    phase = Phase.ACT

                case Phase.ACT:
                    assert ctx is not None
                    phase = await self._act(state, ctx)

                case Phase.OBSERVE:
                    assert ctx is not None
                    phase = await self._observe(state, ctx)

                case Phase.VERIFY:
                    assert ctx is not None
                    verdict = await self._verify_step(state, ctx)
                    if verdict.status != "passed":
                        phase = Phase.RECOVER
                        continue
                    step = state.current_step
                    state.completed.append(StepRecord(step.description, verdict.evidence))
                    state.step_index += 1
                    state.step_attempts = 0
                    if state.has_more_steps:
                        ctx = self._begin_step(state)
                        phase = Phase.ACT
                    else:
                        state.summary = verdict.summary or verdict.reason or "Done."
                        phase = Phase.FINISH

                case Phase.FINISH:
                    return self._result(state, "success", state.summary)

                case Phase.RECOVER:
                    assert verdict is not None
                    outcome = self._recover(state, verdict)
                    if isinstance(outcome, RunResult):
                        return outcome
                    phase = outcome
                    if phase is Phase.ACT:
                        ctx = self._begin_step(state)

    # ---- Plan -------------------------------------------------------------------------------

    async def _plan(self, state: RunState) -> None:
        plan = await self._structured(
            state,
            system=prompts.PLAN_SYSTEM,
            contents=[user_text(prompts.plan_prompt(state, self._tools.describe()))],
            schema=Plan,
            thinking=Thinking.HIGH if state.replans else Thinking.LOW,
            purpose="replan" if state.replans else "plan",
        )
        state.plan = plan
        state.step_index = 0
        state.step_attempts = 0
        if not plan.steps:  # a plain answer: nothing to announce
            return
        if not state.replans:
            self._emit(state, "phase", phase="plan")
        self._emit(
            state,
            "plan",
            steps=[s.model_dump() for s in plan.steps],
            success_criteria=plan.success_criteria,
        )

    def _begin_step(self, state: RunState) -> StepContext:
        return StepContext(contents=[user_text(prompts.step_prompt(state))])

    # ---- Act / Observe ----------------------------------------------------------------------

    async def _act(self, state: RunState, ctx: StepContext) -> Phase:
        if ctx.turns >= self._budget.max_turns_per_step:
            ctx.exhausted = True
            return Phase.VERIFY
        ctx.turns += 1
        # Stay fast by default; think harder only when a previous attempt already failed.
        resp = await self._generate(
            state,
            system=prompts.ACT_SYSTEM,
            contents=ctx.contents,
            tools=self._tools.specs(),
            thinking=Thinking.MEDIUM if state.step_attempts else Thinking.LOW,
            purpose="act",
        )
        ctx.contents.append(resp.content)
        if not resp.tool_calls:
            ctx.claim = resp.text
            return Phase.VERIFY
        ctx.pending = resp.tool_calls
        return Phase.OBSERVE

    async def _observe(self, state: RunState, ctx: StepContext) -> Phase:
        calls = ctx.pending
        results = await self._tools.execute_all(calls)
        for call, result in zip(calls, results, strict=True):
            ctx.record(call, result.ok, result.text, self._budget.stuck_threshold)
            state.tool_log.append(
                ToolRecord(
                    state.step_index + 1,
                    call.name,
                    call.args,
                    result.ok,
                    result.duration_s,
                    result.text,
                )
            )
            self._emit(
                state,
                "tool",
                tool=call.name,
                args=call.args,
                ok=result.ok,
                output=prompts.clip(result.text, 400),
            )
        ctx.contents.append(tool_results_content(calls, [r.payload() for r in results]))
        ctx.pending = ()
        return Phase.VERIFY if ctx.stuck else Phase.ACT

    # ---- Verify -----------------------------------------------------------------------------

    def _is_read_only_success(self, ctx: StepContext) -> bool:
        if not ctx.actions:
            return False
        for action in ctx.actions:
            tool = self._tools.get(action.tool)
            if not action.ok or tool is None or tool.risk is not Risk.READ:
                return False
        return True

    def _run_was_read_only(self, state: RunState) -> bool:
        for record in state.tool_log:
            tool = self._tools.get(record.tool)
            if tool is None or tool.risk is not Risk.READ:
                return False
        return True

    async def _verify_step(self, state: RunState, ctx: StepContext) -> Verdict:
        step = state.current_step
        auto = False
        if ctx.stuck:
            verdict = Verdict(
                status="replan",
                reason="The same action kept returning identical results; no progress is made.",
                evidence=prompts.format_actions(ctx.actions[-3:]),
            )
        elif ctx.exhausted:
            verdict = Verdict(
                status="replan",
                reason=f"No conclusion reached after {ctx.turns - 1} turns on this step.",
                evidence=prompts.format_actions(ctx.actions[-3:]),
            )
        elif (
            self._budget.skip_verify_read_only
            and self._is_read_only_success(ctx)
            and (not state.is_last_step or (ctx.claim and self._run_was_read_only(state)))
        ):
            # Looking things up changes nothing, so there is nothing to verify. A question that
            # only needed lookups is answered straight from the agent's reply.
            verdict = Verdict(
                status="passed",
                reason="Read-only step completed successfully.",
                evidence=prompts.format_actions(ctx.actions, 300),
                summary=ctx.claim,
            )
            auto = True
        else:
            verdict = await self._judge(state, ctx, step.description, step.expected_outcome)
        self._emit_verdict(state, verdict, auto=auto)
        return verdict

    async def _judge(
        self, state: RunState, ctx: StepContext, subject: str, expected: str
    ) -> Verdict:
        plan = state.require_plan()
        is_last = state.is_last_step
        prompt = prompts.verify_prompt(
            subject,
            expected,
            ctx.claim,
            prompts.format_actions(ctx.actions),
            earlier=[(r.description, r.evidence) for r in state.completed],
            goal=state.goal if is_last else "",
            success_criteria=plan.success_criteria if is_last else "",
            voice=state.voice,
        )
        specs = self._tools.specs(read_only=True)
        if not specs or self._budget.verify_probes <= 0:
            return await self._structured(
                state,
                system=prompts.VERIFY_SYSTEM,
                contents=[user_text(prompt)],
                schema=Verdict,
                thinking=Thinking.LOW,
                purpose="verify",
            )
        return await self._verify_with_tools(state, prompt, specs)

    async def _verify_with_tools(
        self, state: RunState, prompt: str, read_specs: list[ToolSpec]
    ) -> Verdict:
        """One call in the common case: the verifier may look at fresh state with read-only tools
        and deliver its verdict through `submit_verdict` in the same turn."""
        contents = [user_text(prompt)]
        probes = self._budget.verify_probes
        for attempt in range(probes + 2):
            last_chance = attempt >= probes
            resp = await self._generate(
                state,
                system=prompts.VERIFY_SYSTEM,
                contents=contents,
                tools=[_VERDICT_TOOL] if last_chance else [*read_specs, _VERDICT_TOOL],
                thinking=Thinking.LOW,
                purpose="verify",
            )
            contents.append(resp.content)
            submitted = next((c for c in resp.tool_calls if c.name == SUBMIT_VERDICT), None)
            if submitted is not None:
                try:
                    return Verdict.model_validate(submitted.args)
                except ValidationError as exc:
                    reply = tool_results_content(
                        resp.tool_calls,
                        [{"ok": False, "error": f"Invalid verdict: {exc.error_count()} errors."}]
                        * len(resp.tool_calls),
                    )
                    contents.append(reply)
                    continue
            if not resp.tool_calls:
                contents.append(user_text("Call submit_verdict now."))
                continue
            results = await asyncio.gather(
                *(self._tools.execute(c, read_only=True) for c in resp.tool_calls)
            )
            for call, result in zip(resp.tool_calls, results, strict=True):
                state.tool_log.append(
                    ToolRecord(
                        state.step_index + 1,
                        call.name,
                        call.args,
                        result.ok,
                        result.duration_s,
                        result.text,
                    )
                )
                self._emit(
                    state,
                    "probe",
                    tool=call.name,
                    args=call.args,
                    ok=result.ok,
                    output=prompts.clip(result.text, 400),
                )
            contents.append(tool_results_content(resp.tool_calls, [r.payload() for r in results]))
        raise LLMError("Verifier never submitted a valid verdict.")

    # ---- Recover ----------------------------------------------------------------------------

    def _recover(self, state: RunState, verdict: Verdict) -> Phase | RunResult:
        state.failures.append(f"Step '{state.current_step.description}': {verdict.reason}")

        if verdict.status == "blocked":
            self._emit(state, "recover", decision="blocked")
            return self._result(state, "blocked", verdict.reason)

        status = verdict.status
        if status == "retry_step":
            if state.step_attempts < self._budget.max_step_retries:
                state.step_attempts += 1
                state.retries += 1
                self._emit(state, "recover", decision="retry_step", attempt=state.step_attempts)
                return Phase.ACT
            status = "replan"

        if state.replans >= self._budget.max_replans:
            self._emit(state, "recover", decision="give_up")
            return self._result(
                state,
                "failed",
                f"Gave up after {state.replans} replans. Last issue: {verdict.reason}",
            )
        state.replans += 1
        self._emit(state, "recover", decision="replan", count=state.replans)
        return Phase.PLAN

    # ---- LLM plumbing -----------------------------------------------------------------------

    def _count_call(self, state: RunState) -> None:
        if state.llm_calls >= self._budget.max_llm_calls:
            raise BudgetExceeded(f"LLM call budget ({self._budget.max_llm_calls}) exhausted.")
        state.llm_calls += 1

    async def _generate(
        self,
        state: RunState,
        *,
        system: str,
        contents: Sequence[types.Content],
        tools: Sequence[ToolSpec] = (),
        thinking: Thinking = Thinking.LOW,
        purpose: str,
    ) -> LLMResponse:
        self._count_call(state)
        resp = await self._llm.generate(
            system=system, contents=contents, tools=tools, thinking=thinking
        )
        self._record_call(state, purpose, resp.info)
        return resp

    async def _structured(
        self,
        state: RunState,
        *,
        system: str,
        contents: Sequence[types.Content],
        schema: type[T],
        thinking: Thinking,
        purpose: str,
    ) -> T:
        self._count_call(state)
        parsed, info = await self._llm.generate_structured(
            system=system, contents=contents, schema=schema, thinking=thinking
        )
        self._record_call(state, purpose, info)
        return parsed

    def _record_call(self, state: RunState, purpose: str, info: CallInfo) -> None:
        u = info.usage
        state.usage += u
        state.calls.append(
            CallRecord(
                purpose,
                info.model,
                u.prompt_tokens,
                u.output_tokens,
                u.thought_tokens,
                info.latency_s,
            )
        )

    # ---- Output -----------------------------------------------------------------------------

    def _result(self, state: RunState, status: RunStatus, summary: str) -> RunResult:
        return RunResult(
            status=status,
            summary=summary,
            run_id=state.run_id,
            steps_completed=len(state.completed),
            llm_calls=state.llm_calls,
            usage=state.usage,
            elapsed_s=time.monotonic() - state.started,
            goal=state.goal,
            retries=state.retries,
            replans=state.replans,
            plan_steps=tuple(st.description for st in state.plan.steps) if state.plan else (),
            calls=tuple(state.calls),
            tools=tuple(state.tool_log),
            verdicts=tuple(state.verdicts),
        )

    def emit_raw(self, kind: str, data: dict[str, Any]) -> None:
        self._sink.emit(Event(kind, data))

    def _emit(self, state: RunState, kind: str, **data: Any) -> None:
        self._sink.emit(Event(kind, {"run_id": state.run_id, **data}))

    def _emit_verdict(self, state: RunState, verdict: Verdict, *, auto: bool = False) -> None:
        state.verdicts.append(
            VerdictRecord(state.step_index + 1, verdict.status, verdict.reason, auto)
        )
        self._emit(
            state,
            "verdict",
            status=verdict.status,
            reason=verdict.reason,
            evidence=prompts.clip(verdict.evidence, 400),
        )
