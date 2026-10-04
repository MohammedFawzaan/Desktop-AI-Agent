from __future__ import annotations

from desktop_ai_agent.agent.state import ActionRecord, RunState

PLAN_SYSTEM = """You are Friday, an AI agent that works on the user's own Windows PC.
If the message needs no tools (a greeting, a question you can answer, small talk), put the full
reply in direct_answer and leave steps empty. Otherwise turn the goal into the FEWEST steps
that the available tools can perform. A step is a checkpoint that gets verified, not a single
tool call: one step may use many tool calls, so merge related actions. Most goals need 1-3
steps; a simple goal is ONE step. Each step needs an expected_outcome that can be checked by
observing real state (never "the model thinks it worked"). You are not told anything about
this PC up front: when a goal depends on a location, the OS, hardware or the user, plan a
step that discovers it with get_system_context (or other read-only tools) instead of
guessing. Do not invent tools or capabilities. When replanning, plan only the remaining
work and avoid approaches that failed."""

ACT_SYSTEM = """You are Friday, the execution engine of an agent that works on the user's own
Windows PC. You are given ONE step of a plan. Carry it out with the tools.
- Work only on the current step; do not skip ahead.
- Never guess a path or a system fact. Call get_system_context to find the real location
  of Desktop, Documents, Downloads etc., the user name, OS or hardware, and use the
  exact paths it returns. Use find_paths / list_dir to locate anything else.
- Prefer the dedicated file tools for reading, writing and listing; use run_command (Windows
  PowerShell, non-interactive) for everything else.
- Call independent tools together in a single turn; call dependent ones in order.
- If the current state is uncertain, observe first with a read-only tool, then act.
- Read every tool result. If a call fails, change your approach or arguments; never repeat an
  identical failing call.
- If a result says 'Blocked' (safety policy, or the user declined), do NOT look for a
  workaround or a different way to do the same thing. Stop and explain plainly.
- Never claim anything the tool outputs do not show.
- When the step's expected outcome is achieved, reply in plain text with NO tool call: what you
  did and what you observed as proof.
- If the user's message is a question or a request for information, your final reply IS the
  answer they see: complete, accurate to the tool output, and in the format and length they
  asked for.
- If the step cannot be done, say so plainly and explain why."""

VERIFY_SYSTEM = """You are a strict verifier for a desktop automation agent.
Judge ONLY from evidence. The agent's claim is not evidence; tool outputs are.
Usually the tool outputs already shown are enough: then call submit_verdict right away. Use the
read-only tools only when the outputs do not prove the current state. Choose passed only if the
evidence shows the expected outcome. Choose retry_step if it is close and fixable by redoing the
step, replan if the approach or plan is wrong, blocked if it is impossible, needs the user, or
an action was blocked by the safety policy or declined by the user.
When a final goal and success criteria are given, passed also means the whole goal is achieved
(earlier verified steps count as evidence), and you must fill `summary` with a short final
answer for the user."""

_PREVIEW = 600


def clip(text: str, limit: int = _PREVIEW) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def format_actions(actions: list[ActionRecord], limit: int = _PREVIEW) -> str:
    if not actions:
        return "(no tool calls were made)"
    return "\n".join(
        f"{i}. {a.tool}({a.args}) -> {'OK' if a.ok else 'FAILED'}: {clip(a.output, limit)}"
        for i, a in enumerate(actions, 1)
    )


def plan_prompt(state: RunState, tool_catalog: str) -> str:
    parts = [f"User message: {state.goal}"]
    parts.append(f"Available tools:\n{tool_catalog or '(none)'}")
    if state.session:
        earlier = "\n".join(
            f"- user: {t.goal} -> {t.status}: {clip(t.summary, 200)}" for t in state.session
        )
        parts.append(f"Earlier in this conversation (for follow-ups like 'it'):\n{earlier}")
    if state.completed:
        done = "\n".join(f"- {r.description}: {clip(r.evidence)}" for r in state.completed)
        parts.append(f"Already completed and verified (do not redo):\n{done}")
    if state.failures:
        failed = "\n".join(f"- {f}" for f in state.failures[-4:])
        parts.append(f"Failed attempts so far:\n{failed}")
    return "\n\n".join(parts)


def step_prompt(state: RunState) -> str:
    assert state.plan is not None
    total = len(state.plan.steps)
    lines: list[str] = []
    for i, step in enumerate(state.plan.steps):
        marker = ">>" if i == state.step_index else "  "
        lines.append(f"{marker} {i + 1}. {step.description}")
    step = state.current_step
    parts = [f"Overall goal: {state.goal}"]
    parts.append("Plan:\n" + "\n".join(lines))
    if state.completed:
        parts.append(
            "Verified so far:\n"
            + "\n".join(f"- {r.description}: {clip(r.evidence, 300)}" for r in state.completed)
        )
    parts.append(
        f"CURRENT STEP ({state.step_index + 1}/{total}): {step.description}\n"
        f"Expected outcome: {step.expected_outcome}"
    )
    if state.failures and state.step_attempts > 0:
        parts.append(
            f"Previous attempt at this step failed verification: {state.failures[-1]}\n"
            "Do not repeat it. Re-observe the current state, then try a different approach."
        )
    return "\n\n".join(parts)


def verify_prompt(
    subject: str,
    expected: str,
    claim: str,
    evidence: str,
    *,
    earlier: list[tuple[str, str]],
    goal: str = "",
    success_criteria: str = "",
) -> str:
    parts = [
        f"Step: {subject}",
        f"Expected outcome: {expected}",
        f"Agent's claim (not evidence): {claim or '(none)'}",
        f"Actions and tool outputs:\n{evidence}",
    ]
    if goal:
        done = "\n".join(f"- {d}: {clip(e, 300)}" for d, e in earlier) or "(none)"
        parts.append(f"Earlier verified steps:\n{done}")
        parts.append(
            f"This is the LAST step. Overall goal: {goal}\n"
            f"Success criteria for the whole goal: {success_criteria}"
        )
    return "\n\n".join(parts)
