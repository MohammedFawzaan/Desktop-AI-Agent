"""The prompts sent to the model: fixed system prompts (in system_prompts/*.md) and the messages
built for each phase from the run's state."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from friday.agent.models import ActionRecord, RunState

_PROMPTS_DIR = Path(__file__).with_name("system_prompts")
_PREVIEW_CHARS = 600


def _load(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


PLAN_SYSTEM = _load("plan")
ACT_SYSTEM = _load("act")
VERIFY_SYSTEM = _load("verify")
SEARCH_SYSTEM = _load("search")
VOICE_STYLE = _load("voice")


def clip(text: str, limit: int = _PREVIEW_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def format_actions(actions: Sequence[ActionRecord], limit: int = _PREVIEW_CHARS) -> str:
    if not actions:
        return "(no tool calls were made)"
    return "\n".join(
        f"{i}. {a.tool}({a.args}) -> {'OK' if a.ok else 'FAILED'}: {clip(a.output, limit)}"
        for i, a in enumerate(actions, 1)
    )


def _conversation(state: RunState) -> str:
    lines = "\n".join(f"- user: {t.goal} -> {clip(t.summary, 200)}" for t in state.session)
    return f"Earlier in this conversation (for follow-ups like 'it'):\n{lines}"


def plan_prompt(state: RunState, tool_catalog: str) -> str:
    parts = [f"User message: {state.goal}"]
    if state.voice:
        parts.append(VOICE_STYLE)
    parts.append(f"Available tools:\n{tool_catalog or '(none)'}")
    if state.session:
        parts.append(_conversation(state))
    if state.completed:
        done = "\n".join(f"- {r.description}: {clip(r.evidence)}" for r in state.completed)
        parts.append(f"Already completed and verified (do not redo):\n{done}")
    if state.failures:
        failed = "\n".join(f"- {f}" for f in state.failures[-4:])
        parts.append(f"Failed attempts so far:\n{failed}")
    return "\n\n".join(parts)


def search_prompt(state: RunState, query: str) -> str:
    parts = [f"User message: {state.goal}"]
    if query.strip() != state.goal.strip():
        parts.append(f"Search for: {query}")
    if state.session:
        parts.append(_conversation(state))
    if state.voice:
        parts.append(VOICE_STYLE)
    return "\n\n".join(parts)


def step_prompt(state: RunState) -> str:
    plan = state.require_plan()
    outline = "\n".join(
        f"{'>>' if i == state.step_index else '  '} {i + 1}. {step.description}"
        for i, step in enumerate(plan.steps)
    )
    step = state.current_step
    parts = [f"Overall goal: {state.goal}"]
    if state.voice:
        parts.append(VOICE_STYLE)
    parts.append(f"Plan:\n{outline}")
    if state.completed:
        done = "\n".join(f"- {r.description}: {clip(r.evidence, 300)}" for r in state.completed)
        parts.append(f"Verified so far:\n{done}")
    parts.append(
        f"CURRENT STEP ({state.step_index + 1}/{len(plan.steps)}): {step.description}\n"
        f"Expected outcome: {step.expected_outcome}"
    )
    if state.failures and state.step_attempts > 0:
        parts.append(
            f"The previous attempt at this step failed verification: {state.failures[-1]}\n"
            "Do not repeat it. Look at the current state again, then try a different approach."
        )
    return "\n\n".join(parts)


def verify_prompt(state: RunState, claim: str, actions: Sequence[ActionRecord]) -> str:
    step = state.current_step
    parts = [
        f"Step: {step.description}",
        f"Expected outcome: {step.expected_outcome}",
        f"Agent's claim (not evidence): {claim or '(none)'}",
        f"Actions and tool outputs:\n{format_actions(actions)}",
    ]
    if state.is_last_step:
        done = "\n".join(f"- {r.description}: {clip(r.evidence, 300)}" for r in state.completed)
        parts.append(f"Earlier verified steps:\n{done or '(none)'}")
        parts.append(
            f"This is the LAST step. Overall goal: {state.goal}\n"
            f"Success criteria for the whole goal: {state.require_plan().success_criteria}"
        )
        if state.voice:
            parts.append(VOICE_STYLE)
    return "\n\n".join(parts)
