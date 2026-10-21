"""Verify phase: an independent check, from the tool outputs, that a step really worked."""

from __future__ import annotations

from friday.agent import prompts
from friday.agent.context import RunContext
from friday.agent.llm import user_text
from friday.agent.models import StepContext, Verdict
from friday.tools.registry import Risk, ToolRegistry


async def verify_step(run: RunContext, tools: ToolRegistry, step: StepContext) -> Verdict:
    if step.out_of_turns:
        verdict = Verdict(
            status="replan",
            reason=f"No result after {step.turns} turns on this step.",
            evidence=prompts.format_actions(step.actions[-3:]),
        )
    elif _only_looked_things_up(run, tools, step):
        # Reading changes nothing, so there is nothing to check. A question that needed only
        # lookups is answered with the actor's own reply.
        verdict = Verdict(
            status="passed",
            reason="Read-only step completed.",
            evidence=prompts.format_actions(step.actions, 300),
            summary=step.claim,
        )
    else:
        verdict = await run.structured(
            purpose="verify",
            system=prompts.VERIFY_SYSTEM,
            contents=[user_text(prompts.verify_prompt(run.state, step.claim, step.actions))],
            schema=Verdict,
        )
    run.emit("verdict", status=verdict.status, reason=verdict.reason)
    return verdict


def _only_looked_things_up(run: RunContext, tools: ToolRegistry, step: StepContext) -> bool:
    """True if every tool call in this step was a successful read. On the last step the whole
    run must have been reads, and the actor must have given an answer."""
    if not step.actions or not all(a.ok and _is_read(tools, a.tool) for a in step.actions):
        return False
    if not run.state.is_last_step:
        return True
    return bool(step.claim) and all(_is_read(tools, name) for name in run.state.tools_used)


def _is_read(tools: ToolRegistry, name: str) -> bool:
    tool = tools.get(name)
    return tool is not None and tool.risk is Risk.READ
