from __future__ import annotations

from friday.agent.loop import Agent, Budget, RunResult, SessionTurn
from friday.monitoring import Event
from friday.tools import Risk, ToolRegistry
from tests.fakes import FakeLLM, act_turn, answer, call, plan, say, submit, verdict


class Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)

    def phases(self) -> list[str]:
        return [e.data["phase"] for e in self.events if e.kind == "phase"]

    def of(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]


def make_tools() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(risk=Risk.WRITE)
    def do_thing() -> str:
        """Change something."""
        return "changed"

    @reg.tool(risk=Risk.READ)
    def check_thing() -> str:
        """Read the state."""
        return "state=changed"

    @reg.tool(risk=Risk.WRITE)
    def broken() -> str:
        """Always fails."""
        raise RuntimeError("nope")

    return reg


async def run(llm: FakeLLM, budget: Budget | None = None) -> tuple[RunResult, Recorder]:
    rec = Recorder()
    # verify_probes=0 uses the single structured verdict call; probing has its own tests.
    budget = budget or Budget(verify_probes=0)
    result = await Agent(llm, make_tools(), budget=budget, sink=rec).run("do the thing")
    return result, rec


async def test_happy_path_walks_every_phase_with_minimal_calls() -> None:
    llm = FakeLLM().queue(
        plan("change it"),
        act_turn(call("do_thing")),
        say("Changed it."),
        verdict("passed", summary="All done."),
    )
    result, rec = await run(llm)
    assert result.status == "success" and result.summary == "All done."
    assert rec.phases() == ["plan", "act", "observe", "act", "verify", "finish"]
    assert result.llm_calls == 4  # plan + 2 act turns + 1 verify


async def test_model_going_quiet_without_evidence_is_not_success() -> None:
    llm = FakeLLM().queue(
        plan("change it"),
        say("Sure, done!"),
        verdict("retry_step", "No tool output proves anything."),
        act_turn(call("do_thing")),
        say("Changed it."),
        verdict("passed", summary="ok"),
    )
    result, rec = await run(llm)
    assert result.status == "success"
    assert "recover" in rec.phases()


async def test_retry_exhaustion_escalates_to_replan_then_succeeds() -> None:
    llm = FakeLLM().queue(
        plan("bad approach"),
        say("a"),
        verdict("retry_step", "no"),
        say("b"),
        verdict("retry_step", "no"),
        say("c"),
        verdict("retry_step", "no"),
        plan("good approach"),
        say("d"),
        verdict("passed", summary="ok"),
    )
    result, rec = await run(llm, Budget(verify_probes=0, max_step_retries=2))
    assert result.status == "success"
    assert rec.phases().count("plan") == 2


async def test_replans_are_capped() -> None:
    llm = FakeLLM().queue(
        plan("one"),
        say("x"),
        verdict("replan", "wrong"),
        plan("two"),
        say("y"),
        verdict("replan", "wrong"),
    )
    result, _ = await run(llm, Budget(verify_probes=0, max_replans=1))
    assert result.status == "failed" and "Gave up" in result.summary


async def test_blocked_verdict_stops_the_run() -> None:
    llm = FakeLLM().queue(plan("one"), say("need password"), verdict("blocked", "needs password"))
    result, _ = await run(llm)
    assert result.status == "blocked" and "password" in result.summary


async def test_last_step_verdict_also_judges_the_whole_goal() -> None:
    llm = FakeLLM().queue(
        plan("one"),
        say("done"),
        verdict("replan", "success criteria not met"),
        plan("extra"),
        say("done"),
        verdict("passed", summary="now really done"),
    )
    result, _ = await run(llm)
    assert result.status == "success" and result.summary == "now really done"


async def test_repeating_identical_failing_action_is_detected_as_stuck() -> None:
    llm = FakeLLM().queue(
        plan("one"),
        act_turn(call("broken")),
        act_turn(call("broken")),
        act_turn(call("broken")),
        plan("another way"),
        say("ok"),
        verdict("passed", summary="ok"),
    )
    result, rec = await run(llm, Budget(verify_probes=0, stuck_threshold=3))
    assert result.status == "success"
    assert rec.of("verdict")[0].data["status"] == "replan"  # deterministic, no LLM verify call


async def test_turn_limit_per_step_forces_recovery() -> None:
    llm = FakeLLM().queue(
        plan("one"),
        act_turn(call("do_thing")),
        act_turn(call("check_thing")),
        plan("two"),
        say("ok"),
        verdict("passed", summary="ok"),
    )
    result, _ = await run(llm, Budget(verify_probes=0, max_turns_per_step=2))
    assert result.status == "success"


async def test_llm_call_budget_is_enforced() -> None:
    llm = FakeLLM().queue(plan("one"), act_turn(call("do_thing")), say("x"), verdict("passed"))
    result, _ = await run(llm, Budget(verify_probes=0, max_llm_calls=2))
    assert result.status == "budget_exceeded"


async def test_read_only_intermediate_steps_skip_the_verifier() -> None:
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
    assert len(rec.of("verdict")) == 2  # one auto-pass for the read, one real verification
    assert llm.structured_calls == 2  # plan + a single verification


async def test_verifier_checks_fresh_state_and_submits_in_the_same_loop() -> None:
    llm = FakeLLM().queue(
        plan("change it"),
        act_turn(call("do_thing")),
        say("Changed it."),
        act_turn(call("check_thing")),  # verifier looks at fresh state...
        submit("passed", summary="ok"),  # ...then submits its verdict
    )
    result, rec = await run(llm, Budget(verify_probes=2))
    assert result.status == "success" and result.summary == "ok"
    assert [e.data["tool"] for e in rec.of("probe")] == ["check_thing"]


async def test_verifier_can_submit_immediately_in_one_call() -> None:
    llm = FakeLLM().queue(
        plan("change it"),
        act_turn(call("do_thing")),
        say("Changed it."),
        submit("passed", summary="ok"),
    )
    result, rec = await run(llm, Budget(verify_probes=2))
    assert result.status == "success" and result.llm_calls == 4
    assert not rec.of("probe")


async def test_verifier_is_nudged_when_it_answers_in_plain_text() -> None:
    llm = FakeLLM().queue(
        plan("change it"),
        act_turn(call("do_thing")),
        say("Changed it."),
        say("Looks fine to me."),
        submit("passed", summary="ok"),
    )
    result, _ = await run(llm, Budget(verify_probes=1))
    assert result.status == "success"


async def test_messages_needing_no_tools_get_a_direct_answer_in_one_call() -> None:
    llm = FakeLLM().queue(answer("Hi! I am Friday."))
    result, rec = await run(llm)
    assert result.status == "success" and result.summary == "Hi! I am Friday."
    assert result.llm_calls == 1 and rec.phases() == ["finish"]  # no planning for a question


async def test_session_history_reaches_the_planner_but_no_hardcoded_environment() -> None:
    llm = FakeLLM().queue(answer("ok"))
    session = [SessionTurn("make notes.txt", "success", "created notes.txt")]
    await Agent(llm, make_tools()).run("now delete it", session=session)
    prompt = llm.seen[0]
    assert "make notes.txt" in prompt and "created notes.txt" in prompt
    assert "Environment" not in prompt and "Desktop" not in prompt


async def test_result_carries_per_call_and_per_tool_metrics() -> None:
    llm = FakeLLM().queue(
        plan("change it"),
        act_turn(call("do_thing")),
        say("Changed it."),
        verdict("passed", summary="ok"),
    )
    result, _ = await run(llm)
    assert [c.purpose for c in result.calls] == ["plan", "act", "act", "verify"]
    assert all(c.model == "fake-model" and c.prompt_tokens == 10 for c in result.calls)
    assert len(result.tools) == 1 and result.tools[0].tool == "do_thing" and result.tools[0].ok
    assert result.tools[0].duration_s >= 0 and result.verdicts[0].status == "passed"
    assert result.usage.prompt_tokens == 40 and result.plan_steps == ("change it",)


async def test_lookup_only_questions_are_answered_without_the_verifier() -> None:
    llm = FakeLLM().queue(
        plan("look up the state"),
        act_turn(call("check_thing")),
        say("The state is: changed."),
    )
    result, rec = await run(llm)
    assert result.status == "success" and result.summary == "The state is: changed."
    assert result.llm_calls == 3 and llm.structured_calls == 1  # plan, 2 act turns, no verify
    assert rec.of("verdict")[0].data["status"] == "passed"


async def test_lookup_shortcut_never_applies_once_anything_was_changed() -> None:
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
    assert result.status == "success" and result.summary == "done"
    assert llm.structured_calls == 3  # plan + both steps verified


async def test_a_claim_with_no_tool_calls_still_needs_verification() -> None:
    llm = FakeLLM().queue(plan("change it"), say("Done!"), verdict("retry_step", "no proof"))
    llm.queue(act_turn(call("do_thing")), say("Changed."), verdict("passed", summary="ok"))
    result, _ = await run(llm)
    assert result.status == "success" and llm.structured_calls == 3


async def test_voice_chat_asks_for_short_spoken_replies_only_when_speaking() -> None:
    spoken = FakeLLM().queue(answer("ok"))
    await Agent(spoken, make_tools()).run("hello", voice=True)
    assert "Reply style" in spoken.seen[0]
    typed = FakeLLM().queue(answer("ok"))
    await Agent(typed, make_tools()).run("hello")
    assert "Reply style" not in typed.seen[0]


async def test_llm_calls_made_inside_a_tool_count_in_the_metrics() -> None:
    from dataclasses import replace

    from friday.tools import ToolResult
    from tests.fakes import INFO

    reg = make_tools()

    @reg.tool(risk=Risk.READ)
    async def look() -> ToolResult:
        """Pretend vision tool."""
        return replace(ToolResult.success("a red button"), calls=(INFO,))

    llm = FakeLLM().queue(
        plan("look", "change it"),
        act_turn(call("look")),
        say("Saw it."),
        act_turn(call("do_thing")),
        say("Done."),
        verdict("passed", summary="ok"),
    )
    result = await Agent(llm, reg, budget=Budget(verify_probes=0)).run("x")
    purposes = [c.purpose for c in result.calls]
    assert "vision" in purposes and result.llm_calls == len(result.calls)


async def test_time_sensitive_questions_are_never_answered_from_memory() -> None:
    llm = FakeLLM().queue(
        answer("He was detained."),  # the model tries to answer a news question from memory
        plan("look it up"),
        act_turn(call("check_thing")),
        say("Per the article: detained on 9 October."),
    )
    rec = Recorder()
    result = await Agent(llm, make_tools(), budget=Budget(verify_probes=0), sink=rec).run(
        "What happened to Abhijit Dipke on 9 October 2026?"
    )
    assert result.status == "success" and llm.structured_calls == 2
    assert any(e.kind == "grounding" for e in rec.events)
    assert "depends on current" in llm.seen[1]


async def test_stable_knowledge_still_gets_one_call() -> None:
    llm = FakeLLM().queue(answer("Recursion is a function calling itself."))
    rec = Recorder()
    result = await Agent(llm, make_tools(), sink=rec).run("Explain recursion simply")
    assert result.llm_calls == 1 and not any(e.kind == "grounding" for e in rec.events)
