from __future__ import annotations

import pytest

from friday.agent import prompts
from friday.agent.loop import needs_fresh_data


@pytest.mark.parametrize(
    "message",
    [
        "What happened to Abhijit Dipke on 9th October 2026?",
        "Convert 0.0225 dollars into Indian rupees",
        "What's the weather in Hyderabad?",
        "latest news about AI",
        "What is the price of Bitcoin today?",
        "Who won the match yesterday?",
        "What's the current version of Python?",
    ],
)
def test_time_sensitive_questions_are_detected(message: str) -> None:
    assert needs_fresh_data(message)


@pytest.mark.parametrize(
    "message",
    [
        "Explain recursion in simple words",
        "What is a REST API?",
        "Tell me a joke",
        "How do I center a div in CSS?",
        "Hello Friday, how are you?",
    ],
)
def test_stable_questions_are_not_flagged(message: str) -> None:
    assert not needs_fresh_data(message)


def test_prompts_carry_the_grounding_rules() -> None:
    assert "never answer from memory" in prompts.PLAN_SYSTEM
    assert "html.duckduckgo.com" in prompts.ACT_SYSTEM
    assert "must appear in a tool output" in prompts.ACT_SYSTEM
    assert "supported by the tool outputs" in prompts.VERIFY_SYSTEM
