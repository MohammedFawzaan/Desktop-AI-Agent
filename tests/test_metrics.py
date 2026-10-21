from __future__ import annotations

from friday.monitoring import CallRecord, estimate_cost


def record(model: str, prompt: int = 0, output: int = 0, thinking: int = 0) -> CallRecord:
    return CallRecord("act", model, prompt, output, thinking, 1.0)


def test_cost_uses_the_price_table_and_bills_thinking_as_output() -> None:
    cost = estimate_cost([record("gemini-3.5-flash", output=500_000, thinking=500_000)])
    assert cost == 9.00
    assert estimate_cost([record("gemini-3.5-flash", prompt=1_000_000)]) == 1.50


def test_an_unknown_model_gives_no_estimate() -> None:
    assert estimate_cost([record("gemini-3.5-flash"), record("mystery-model")]) is None
