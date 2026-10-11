from __future__ import annotations

from datetime import date

from friday.monitoring import CallRecord, estimate_cost, price_for


def rec(model: str, i: int = 1_000_000, o: int = 0, t: int = 0) -> CallRecord:
    return CallRecord("act", model, i, o, t, 1.0)


def test_promo_price_switches_at_2027() -> None:
    assert price_for("gemini-3.8-flash", date(2026, 10, 1)) == (0.75, 3.75)
    assert price_for("gemini-3.8-flash", date(2027, 1, 1)) == (1.50, 7.50)


def test_thinking_tokens_are_billed_as_output() -> None:
    cost = estimate_cost([rec("gemini-3.5-flash", i=0, o=500_000, t=500_000)])
    assert cost == 9.00


def test_unknown_model_gives_no_estimate() -> None:
    assert estimate_cost([rec("gemini-3.5-flash"), rec("mystery-model")]) is None
