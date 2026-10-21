"""The data of a run: the plan, the verifier's verdict, the limits, and the run's state and result.

`Plan` and `Verdict` are what the model must return (validated JSON); the rest are plain records.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, Field, model_validator

from friday.agent.llm import ToolCall, Usage
from friday.monitoring.metrics import CallRecord


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
    can_be_outdated: bool = Field(
        default=False,
        description=(
            "True if a correct answer depends on information that may have changed since your "
            "training or that you cannot know for sure (anything current, recent or live)."
        ),
    )
    direct_answer: str = Field(
        default="",
        description="Only for stable knowledge that needs no tools: the full reply to the user.",
    )
    search_query: str = Field(
        default="",
        description=(
            "Only for a question that needs current or online information and no action on the "
            "PC: a precise web search query that answers it."
        ),
    )
    steps: list[Step] = Field(default_factory=list[Step], max_length=12)
    success_criteria: str = Field(
        default="", description="Observable conditions that prove the whole goal is achieved."
    )

    @model_validator(mode="after")
    def _has_an_outcome(self) -> Plan:
        if not (self.steps or self.direct_answer.strip() or self.search_query.strip()):
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
        default="", description="Final step only: the answer for the user, from the evidence."
    )


@dataclass(frozen=True, slots=True)
class Budget:
    """Limits that stop a run from going on forever."""

    max_llm_calls: int = 40
    max_turns_per_step: int = 15
    max_step_retries: int = 2
    max_replans: int = 2
    timeout_s: float = 900.0


@dataclass(frozen=True, slots=True)
class ActionRecord:
    tool: str
    args: dict[str, Any]
    ok: bool
    output: str


@dataclass(slots=True)
class StepContext:
    """The conversation and actions of the step being worked on."""

    contents: list[types.Content]
    actions: list[ActionRecord] = field(default_factory=list[ActionRecord])
    pending: tuple[ToolCall, ...] = ()  # tool calls to run in the next Observe
    claim: str = ""  # what the actor said when it finished
    turns: int = 0
    out_of_turns: bool = False


@dataclass(frozen=True, slots=True)
class StepRecord:
    description: str
    evidence: str


@dataclass(frozen=True, slots=True)
class SessionTurn:
    """An earlier message in the conversation, so follow-ups like 'now delete it' make sense."""

    goal: str
    status: str
    summary: str


RunStatus = Literal["success", "failed", "blocked", "budget_exceeded"]


@dataclass(slots=True)
class RunState:
    goal: str
    run_id: str
    voice: bool = False
    session: tuple[SessionTurn, ...] = ()
    plan: Plan | None = None
    step_index: int = 0
    step_attempts: int = 0
    replans: int = 0
    completed: list[StepRecord] = field(default_factory=list[StepRecord])
    failures: list[str] = field(default_factory=list[str])
    tools_used: list[str] = field(default_factory=list[str])
    calls: list[CallRecord] = field(default_factory=list[CallRecord])
    usage: Usage = field(default_factory=Usage)
    started: float = field(default_factory=time.monotonic)

    @property
    def llm_calls(self) -> int:
        return len(self.calls)

    def require_plan(self) -> Plan:
        assert self.plan is not None
        return self.plan

    @property
    def current_step(self) -> Step:
        return self.require_plan().steps[self.step_index]

    @property
    def has_more_steps(self) -> bool:
        return self.step_index < len(self.require_plan().steps)

    @property
    def is_last_step(self) -> bool:
        return self.step_index == len(self.require_plan().steps) - 1


@dataclass(frozen=True, slots=True)
class RunResult:
    status: RunStatus
    summary: str
    run_id: str
    goal: str
    steps_completed: int
    usage: Usage
    elapsed_s: float
    calls: tuple[CallRecord, ...] = ()

    @property
    def llm_calls(self) -> int:
        return len(self.calls)
