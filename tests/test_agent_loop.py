from __future__ import annotations

from dataclasses import replace

from friday.agent.loop import Agent
from friday.agent.models import Budget, Plan, RunResult, SessionTurn, Step
from friday.monitoring import Event
from friday.tools.registry import Risk, ToolRegistry, ToolResult
from tests.fakes import (
    INFO,
    FakeLLM,
    act_turn,
    allow_all,
    answer,
    call,
    found,
    make_tools,
    plan,
    say,
    verdict,
)


class Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)

    def phases(self) -> list[str]:
        return [e.data["phase"] for e in self.events if e.kind == "phase"]

    def of(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]


async def run(
    llm: FakeLLM, budget: Budget | None = None, *, tools: ToolRegistry | None = None
) -> tuple[RunResult, Recorder]:
    rec = Recorder()
    agent = Agent(llm, tools or make_tools(), budget=budget, sink=rec)
    return await agent.run("do the thing"), rec


# ---- the loop ---------------------------------------------------------------------------------


async def test_happy_path_walks_every_phase() -> None:
    llm = FakeLLM().queue(
        plan("change it"),
        act_turn(call("do_thing")),
        say("Changed it."),
        verdict("passed", summary="All done."),
    )
    result, rec = await run(llm)
    assert result.status == "success" and result.summary == "All done."
    assert rec.phases() == ["act", "observe", "act", "verify", "finish"]
    assert [c.purpose for c in result.calls] == ["plan", "act", "act", "verify"]
    assert result.usage.prompt_tokens == 40


async def test_a_claim_without_evidence_is_not_success() -> None:
    llm = FakeLLM().queue(
        plan("change it"),
        say("Sure, done!"),
        verdict("retry_step", "No tool output proves anything."),
        act_turn(call("do_thing")),
        say("Changed it."),
        verdict("passed", summary="ok"),
    )
    result, rec = await run(llm)
    assert result.status == "success" and "recover" in rec.phases()


async def test_used_up_retries_lead_to_a_new_plan() -> None:
    llm = FakeLLM().queue(
        plan("bad approach"),
        say("a"),
        verdict("retry_step", "no"),
        say("b"),
        verdict("retry_step", "no"),
        plan("good approach"),
        say("c"),
        verdict("passed", summary="ok"),
    )
    result, rec = await run(llm, Budget(max_step_retries=1))
    assert result.status == "success" and len(rec.of("plan")) == 2


async def test_new_plans_are_capped() -> None:
    llm = FakeLLM().queue(
        plan("one"), say("x"), verdict("replan", "wrong"), plan("two"), say("y"), verdict("replan")
    )
    result, _ = await run(llm, Budget(max_replans=1))
    assert result.status == "failed" and "Gave up" in result.summary


async def test_blocked_stops_the_run() -> None:
    llm = FakeLLM().queue(plan("one"), say("need password"), verdict("blocked", "needs password"))
    result, _ = await run(llm)
    assert result.status == "blocked" and "password" in result.summary


async def test_turn_limit_per_step_forces_a_new_plan() -> None:
    llm = FakeLLM().queue(
        plan("one"),
        act_turn(call("do_thing")),
        act_turn(call("broken")),
        plan("two"),
        say("ok"),
        verdict("passed", summary="ok"),
    )
    result, rec = await run(llm, Budget(max_turns_per_step=2))
    assert result.status == "success"
    assert rec.of("verdict")[0].data["status"] == "replan"  # decided without a model call


async def test_model_call_budget_is_enforced() -> None:
    llm = FakeLLM().queue(plan("one"), act_turn(call("do_thing")), say("x"), verdict("passed"))
    result, _ = await run(llm, Budget(max_llm_calls=2))
    assert result.status == "budget_exceeded"


# ---- verification shortcuts --------------------------------------------------------------


async def test_read_only_steps_skip_the_verifier() -> None:
    llm = FakeLLM().queue(
        plan("look", "change it"),
        act_turn(call("check_thing")),
        say("Looked."),
        act_turn(call("do_thing")),
        say("Changed."),
        verdict("passed", summary="ok"),
    )
    result, rec = await run(llm)
    assert result.status == "success" and result.steps_completed == 2
    assert len(rec.of("verdict")) == 2 and llm.structured_calls == 2  # plan + one real check


async def test_a_lookup_only_question_is_answered_without_the_verifier() -> None:
    llm = FakeLLM().queue(
        plan("look up the state"), act_turn(call("check_thing")), say("The state is: changed.")
    )
    result, _ = await run(llm)
    assert result.summary == "The state is: changed." and llm.structured_calls == 1


async def test_the_shortcut_never_applies_once_something_was_changed() -> None:
    llm = FakeLLM().queue(
        plan("change it", "look at it"),
        act_turn(call("do_thing")),
        say("Changed."),
        verdict("passed"),
        act_turn(call("check_thing")),
        say("It is changed."),
        verdict("passed", summary="done"),
    )
    result, _ = await run(llm)
    assert result.summary == "done" and llm.structured_calls == 3


# ---- answers without steps ---------------------------------------------------------------


async def test_stable_knowledge_is_answered_in_one_call() -> None:
    llm = FakeLLM().queue(answer("Recursion is a function calling itself."))
    result, rec = await run(llm)
    assert result.summary == "Recursion is a function calling itself."
    assert result.llm_calls == 1 and llm.web_searches == 0 and rec.phases() == ["finish"]


async def test_a_question_that_needs_the_web_takes_one_search() -> None:
    llm = FakeLLM().queue(
        Plan(search_query="USD to INR today"), found("1 USD = 96.78 INR today.", "XE", "Wise")
    )
    result, rec = await run(llm)
    assert result.summary == "1 USD = 96.78 INR today.\n\nSources: XE, Wise"
    assert [c.purpose for c in result.calls] == ["plan", "search"]
    assert rec.of("search")[0].data["query"] == "USD to INR today"


async def test_an_answer_that_can_be_outdated_comes_from_the_web_not_memory() -> None:
    llm = FakeLLM().queue(
        Plan(direct_answer="He was detained.", can_be_outdated=True),
        found("Reuters reports he was released on 10 October.", "Reuters"),
    )
    result, _ = await run(llm)
    assert result.summary.startswith("Reuters reports") and llm.web_searches == 1


async def test_spoken_answers_ask_for_a_short_style_and_leave_out_sources() -> None:
    llm = FakeLLM().queue(Plan(search_query="rate"), found("96.78 rupees.", "XE"))
    result = await Agent(llm, make_tools()).run("dollar rate?", voice=True)
    assert result.summary == "96.78 rupees." and "Reply style" in llm.prompts[0]


async def test_the_conversation_so_far_reaches_the_planner() -> None:
    llm = FakeLLM().queue(answer("ok"))
    session = [SessionTurn("make notes.txt", "success", "created notes.txt")]
    await Agent(llm, make_tools()).run("now delete it", session=session)
    assert "make notes.txt" in llm.prompts[0] and "created notes.txt" in llm.prompts[0]


# ---- tools ------------------------------------------------------------------------------


def grouped_tools() -> ToolRegistry:
    tools = ToolRegistry(allow_all)

    @tools.tool(risk=Risk.WRITE)
    def write_note() -> str:
        """pc tool"""
        return "ok"

    @tools.tool(risk=Risk.WRITE, group="apps")
    def click_button() -> str:
        """apps tool"""
        return "ok"

    @tools.tool(risk=Risk.READ, group="web")
    def read_page() -> str:
        """web tool"""
        return "page"

    return tools


def one_step(*groups: str) -> Plan:
    step = Step(description="do it", expected_outcome="done", tools=list(groups))
    return Plan(steps=[step], success_criteria="done")


async def test_a_step_only_gets_the_tool_groups_it_needs() -> None:
    llm = FakeLLM().queue(one_step("web"), say("Read it."), verdict("passed", summary="ok"))
    await run(llm, tools=grouped_tools())
    assert sorted(llm.tool_names[0]) == ["read_page", "write_note"]  # 'pc' is always included


async def test_a_retry_gets_every_tool() -> None:
    llm = FakeLLM().queue(
        one_step("web"),
        say("Could not."),
        verdict("retry_step", "needs an app"),
        act_turn(call("click_button")),
        say("Clicked."),
        verdict("passed", summary="ok"),
    )
    result, _ = await run(llm, tools=grouped_tools())
    assert result.status == "success" and len(llm.tool_names[1]) == 3


async def test_model_calls_made_inside_a_tool_are_counted() -> None:
    tools = make_tools()

    @tools.tool(risk=Risk.READ)
    async def look() -> ToolResult:
        """Pretend vision tool."""
        return replace(ToolResult.success("a red button"), calls=(INFO,), calls_purpose="vision")

    llm = FakeLLM().queue(
        plan("look", "change it"),
        act_turn(call("look")),
        say("Saw it."),
        act_turn(call("do_thing")),
        say("Done."),
        verdict("passed", summary="ok"),
    )
    result, _ = await run(llm, tools=tools)
    assert "vision" in [c.purpose for c in result.calls]


def test_the_system_prompts_carry_the_grounding_rules() -> None:
    from friday.agent import prompts

    assert "can_be_outdated" in prompts.PLAN_SYSTEM
    assert (
        "web_search" in prompts.ACT_SYSTEM and "must appear in a tool output" in prompts.ACT_SYSTEM
    )
    assert "supported" in prompts.VERIFY_SYSTEM and "never from memory" in prompts.SEARCH_SYSTEM
