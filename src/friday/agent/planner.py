"""Plan phase: turn the user's message into a direct answer, a web search, or a list of steps."""

from __future__ import annotations

from friday.agent import prompts
from friday.agent.context import RunContext
from friday.agent.llm import Thinking, user_text
from friday.agent.models import Plan
from friday.tools.registry import ToolRegistry


async def make_plan(run: RunContext, tools: ToolRegistry) -> Plan:
    """Make the plan (or a new one after a failure) and start at its first step."""
    state = run.state
    replanning = state.replans > 0
    plan = await run.structured(
        purpose="replan" if replanning else "plan",
        system=prompts.PLAN_SYSTEM,
        contents=[user_text(prompts.plan_prompt(state, tools.describe()))],
        schema=Plan,
        thinking=Thinking.HIGH if replanning else Thinking.LOW,
    )
    state.plan = plan
    state.step_index = 0
    state.step_attempts = 0
    if plan.steps:
        run.emit("plan", steps=[s.description for s in plan.steps])
    return plan


async def answer_question(run: RunContext, plan: Plan) -> str:
    """Answer a message that needs no steps. Anything that could be outdated is answered from a
    live web search, even if the planner also wrote an answer from memory."""
    query = plan.search_query.strip() or (run.state.goal if plan.can_be_outdated else "")
    if not query:
        return plan.direct_answer.strip()
    run.emit("search", query=query)
    resp = await run.generate(
        purpose="search",
        system=prompts.SEARCH_SYSTEM,
        contents=[user_text(prompts.search_prompt(run.state, query))],
        web_search=True,
    )
    answer = resp.text or "I could not find that on the web."
    if resp.sources and not run.state.voice:  # sources are shown, not read aloud
        answer += "\n\nSources: " + ", ".join(s.title for s in resp.sources[:5])
    return answer
