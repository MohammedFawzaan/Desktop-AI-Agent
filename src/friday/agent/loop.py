"""The agent harness: Plan -> Act -> Observe -> Verify -> Recover -> Continue/Finish.

Contents, top to bottom: the run's data (plan, verdict, budget, run state), the check for
time-sensitive questions, and the `Agent` that drives the phases and makes every model call.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, Field, ValidationError, model_validator

from friday.agent import prompts
from friday.agent.llm import (
    LLM,
    CallInfo,
    LLMError,
    LLMResponse,
    T,
    Thinking,
    ToolCall,
    ToolSpec,
    Usage,
    tool_results_content,
    user_text,
)
from friday.monitoring.events import Event, EventSink, NullSink
from friday.monitoring.metrics import CallRecord, ToolRecord, VerdictRecord
from friday.tools.registry import Risk, ToolRegistry, ToolResult

# ---- Run data -------------------------------------------------------------------------------


class Phase(StrEnum):
    PLAN = "plan"
    ACT = "act"
    OBSERVE = "observe"
    VERIFY = "verify"
    RECOVER = "recover"
    FINISH = "finish"


class Step(BaseModel):
    description: str = Field(description="One concrete action-oriented step.")
    expected_outcome: str = Field(
        description="Observable state that proves this step succeeded, checkable with tools."
    )
    tools: list[str] = Field(
        default_factory=list[str],
        description="Tool groups this step needs (from the catalog), e.g. ['pc'] or ['pc', 'web'].",
    )


class Plan(BaseModel):
    direct_answer: str = Field(
        default="",
        description=(
            "Only when the message needs NO tools (greeting, question, chat): the full reply "
            "to the user, with steps left empty. Otherwise leave empty."
        ),
    )
    search_query: str = Field(
        default="",
        description=(
            "Only when the message is a question that needs current or online information and "
            "no action on the PC: a precise web search query that answers it, with steps and "
            "direct_answer left empty."
        ),
    )
    steps: list[Step] = Field(default_factory=list[Step], max_length=12)
    success_criteria: str = Field(
        default="", description="Observable conditions that prove the whole goal is achieved."
    )

    @model_validator(mode="after")
    def _needs_steps_or_answer(self) -> Plan:
        if not self.steps and not self.direct_answer.strip() and not self.search_query.strip():
            raise ValueError("Provide steps, a search_query, or a direct_answer.")
        return self


class Verdict(BaseModel):
    status: Literal["passed", "retry_step", "replan", "blocked"] = Field(
        description=(
            "passed: evidence proves the expected outcome. "
            "retry_step: not achieved but fixable by retrying this step. "
            "replan: this approach or plan is wrong. "
            "blocked: impossible, or needs the user (input, permission, missing info)."
        )
    )
    reason: str = Field(description="One or two sentences justifying the status.")
    evidence: str = Field(description="Concrete facts from tool output that support the status.")
    summary: str = Field(
        default="", description="Final-answer only: what was accomplished, for the user."
    )


@dataclass(slots=True)
class Budget:
    max_llm_calls: int = 40
    max_turns_per_step: int = 15
    max_step_retries: int = 2
    max_replans: int = 2
    verify_probes: int = 2
    skip_verify_read_only: bool = True
    stuck_threshold: int = 3
    timeout_s: float = 900.0


@dataclass(slots=True)
class ActionRecord:
    tool: str
    args: dict[str, Any]
    ok: bool
    output: str


@dataclass(slots=True)
class StepContext:
    contents: list[types.Content]
    actions: list[ActionRecord] = field(default_factory=list[ActionRecord])
    pending: tuple[ToolCall, ...] = ()
    claim: str = ""
    turns: int = 0
    stuck: bool = False
    exhausted: bool = False
    _last_fingerprint: tuple[str, str, str] | None = None
    _repeats: int = 0

    def record(self, call: ToolCall, ok: bool, output: str, threshold: int) -> None:
        self.actions.append(ActionRecord(call.name, call.args, ok, output))
        fp = (call.name, repr(sorted(call.args.items())), output)
        self._repeats = self._repeats + 1 if fp == self._last_fingerprint else 1
        self._last_fingerprint = fp
        if self._repeats >= threshold:
            self.stuck = True


@dataclass(slots=True)
class StepRecord:
    description: str
    evidence: str


@dataclass(frozen=True, slots=True)
class SessionTurn:
    """One earlier task in the chat, so follow-ups like 'now delete it' make sense."""

    goal: str
    status: str
    summary: str


@dataclass(slots=True)
class RunState:
    goal: str
    run_id: str
    plan: Plan | None = None
    step_index: int = 0
    step_attempts: int = 0
    replans: int = 0
    completed: list[StepRecord] = field(default_factory=list[StepRecord])
    failures: list[str] = field(default_factory=list[str])
    llm_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    started: float = field(default_factory=time.monotonic)
    summary: str = ""
    retries: int = 0
    voice: bool = False
    session: tuple[SessionTurn, ...] = ()
    calls: list[CallRecord] = field(default_factory=list[CallRecord])
    tool_log: list[ToolRecord] = field(default_factory=list[ToolRecord])
    verdicts: list[VerdictRecord] = field(default_factory=list[VerdictRecord])

    @property
    def current_step(self) -> Step:
        assert self.plan is not None
        return self.plan.steps[self.step_index]

    def require_plan(self) -> Plan:
        assert self.plan is not None
        return self.plan

    @property
    def is_last_step(self) -> bool:
        return self.step_index == len(self.require_plan().steps) - 1

    @property
    def has_more_steps(self) -> bool:
        assert self.plan is not None
        return self.step_index < len(self.plan.steps)


RunStatus = Literal["success", "failed", "blocked", "budget_exceeded"]


@dataclass(frozen=True, slots=True)
class RunResult:
    status: RunStatus
    summary: str
    run_id: str
    steps_completed: int
    llm_calls: int
    usage: Usage
    elapsed_s: float
    goal: str = ""
    retries: int = 0
    replans: int = 0
    plan_steps: tuple[str, ...] = ()
    calls: tuple[CallRecord, ...] = ()
    tools: tuple[ToolRecord, ...] = ()
    verdicts: tuple[VerdictRecord, ...] = ()


SUBMIT_VERDICT = "submit_verdict"
_VERDICT_TOOL = ToolSpec(
    SUBMIT_VERDICT,
    "Submit your final verdict. Call this once you have enough evidence.",
    Verdict.model_json_schema(),
)


# ---- Grounding: never answer time-sensitive questions from memory ----------------------------


_FRESH = re.compile(
    r"\b(today|tonight|yesterday|tomorrow|this (week|month|year)|right now|nowadays"
    r"|latest|recent(ly)?|current(ly)?|news|headlines?|happened|happening|breaking"
    r"|updates?|prices?|cost of|exchange rates?|interest rates?|stocks?|share price|crypto"
    r"|bitcoin|weather|temperature|forecast|scores?|standings|elections?|released?"
    r"|launch(ed|es)?|version)\b"
    r"|\bconvert\b.*\b(dollars?|rupees?|euros?|pounds?|yen|usd|inr|eur|gbp|currency)\b"
    r"|\b20[2-9]\d\b",
    re.IGNORECASE,
)


def needs_fresh_data(message: str) -> bool:
    return bool(_FRESH.search(message))


class BudgetExceeded(Exception):
    pass


def trim_old_observations(contents: list[types.Content], keep: int = 2, limit: int = 600) -> None:
    """Shorten all but the latest tool results in a step, so each turn resends less."""
    tool_turns = [c for c in contents if c.parts and any(p.function_response for p in c.parts)]
    for content in tool_turns[:-keep]:
        for part in content.parts or []:
            response = part.function_response.response if part.function_response else None
            if not response:
                continue
            for key in ("output", "error"):
                value = response.get(key)
                if isinstance(value, str) and len(value) > limit:
                    response[key] = value[:limit] + f"... [trimmed, was {len(value)} chars]"


# ---- The agent ------------------------------------------------------------------------------


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
                    plan = await self._plan(state)
                    if not plan.steps:
                        state.summary = await self._answer(state, plan)
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

    async def _plan(self, state: RunState) -> Plan:
        """Make (or remake) the plan: steps, or a question answered straight away."""
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
            return plan
        if not state.replans:
            self._emit(state, "phase", phase="plan")
        self._emit(
            state,
            "plan",
            steps=[s.model_dump() for s in plan.steps],
            success_criteria=plan.success_criteria,
        )
        return plan

    async def _answer(self, state: RunState, plan: Plan) -> str:
        """Answer a question without steps. Anything that can change is answered from a live
        web search, even when the planner tried to answer it from memory."""
        query = plan.search_query.strip()
        if not query and needs_fresh_data(state.goal):
            query = state.goal
        if not query:
            return plan.direct_answer.strip()
        self._emit(state, "search", query=query)
        resp = await self._generate(
            state,
            system=prompts.SEARCH_SYSTEM,
            contents=[user_text(prompts.search_prompt(state, query))],
            web_search=True,
            purpose="search",
        )
        answer = resp.text or "I could not find that on the web."
        if resp.sources and not state.voice:
            answer += "\n\nSources: " + ", ".join(s.title for s in resp.sources[:5])
        return answer

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
            tools=self._tools.specs(groups=self._step_groups(state)),
            thinking=Thinking.MEDIUM if state.step_attempts else Thinking.LOW,
            purpose="act",
        )
        ctx.contents.append(resp.content)
        if not resp.tool_calls:
            ctx.claim = resp.text
            return Phase.VERIFY
        ctx.pending = resp.tool_calls
        return Phase.OBSERVE

    def _step_groups(self, state: RunState) -> set[str] | None:
        """Only the tool groups the planner said this step needs; all of them on a retry."""
        if state.plan is None or not state.has_more_steps or state.step_attempts:
            return None
        known = set(self._tools.groups())
        wanted = {g.strip().lower() for g in state.current_step.tools} & known
        return (wanted | {"pc"}) if wanted else None

    async def _observe(self, state: RunState, ctx: StepContext) -> Phase:
        calls = ctx.pending
        results = await self._tools.execute_all(calls)
        for call, result in zip(calls, results, strict=True):
            ctx.record(call, result.ok, result.text, self._budget.stuck_threshold)
            self._record_tool(state, call, result, "tool")
        ctx.contents.append(tool_results_content(calls, [r.payload() for r in results]))
        trim_old_observations(ctx.contents)
        ctx.pending = ()
        return Phase.VERIFY if ctx.stuck else Phase.ACT

    # ---- Verify -----------------------------------------------------------------------------

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
        specs = self._tools.specs(read_only=True, groups=self._step_groups(state))
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
                self._record_tool(state, call, result, "probe")
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

    # ---- Model calls ------------------------------------------------------------------------

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
        web_search: bool = False,
        purpose: str,
    ) -> LLMResponse:
        self._count_call(state)
        resp = await self._llm.generate(
            system=system, contents=contents, tools=tools, thinking=thinking, web_search=web_search
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

    def _record_tool(
        self, state: RunState, call: ToolCall, result: ToolResult, kind: Literal["tool", "probe"]
    ) -> None:
        """Log a tool call (and any model calls it made, such as vision) and show it live."""
        for info in result.calls:
            state.llm_calls += 1
            self._record_call(state, result.calls_purpose, info)
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
            kind,
            tool=call.name,
            args=call.args,
            ok=result.ok,
            output=prompts.clip(result.text, 400),
        )

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
