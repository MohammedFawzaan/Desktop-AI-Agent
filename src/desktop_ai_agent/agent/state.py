from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, Field, model_validator

from desktop_ai_agent.llm.base import ToolCall, Usage
from desktop_ai_agent.obs.metrics import CallRecord, ToolRecord, VerdictRecord


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


class Plan(BaseModel):
    direct_answer: str = Field(
        default="",
        description=(
            "Only when the message needs NO tools (greeting, question, chat): the full reply "
            "to the user, with steps left empty. Otherwise leave empty."
        ),
    )
    steps: list[Step] = Field(default_factory=list[Step], max_length=12)
    success_criteria: str = Field(
        default="", description="Observable conditions that prove the whole goal is achieved."
    )

    @model_validator(mode="after")
    def _needs_steps_or_answer(self) -> Plan:
        if not self.steps and not self.direct_answer.strip():
            raise ValueError("Provide steps, or direct_answer when no tools are needed.")
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
    max_turns_per_step: int = 10
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
