from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class CallRecord:
    purpose: str
    model: str
    prompt_tokens: int
    output_tokens: int
    thought_tokens: int
    latency_s: float


@dataclass(frozen=True, slots=True)
class ToolRecord:
    step: int
    tool: str
    args: dict[str, Any]
    ok: bool
    duration_s: float
    output: str


@dataclass(frozen=True, slots=True)
class VerdictRecord:
    step: int
    status: str
    reason: str
    auto: bool = False


# USD per 1M tokens (input, output). Thinking tokens are billed as output.
# Source: Google AI Studio paid-tier pricing page; 3.7/3.8 Flash have promotional pricing.
_PROMO_END = date(2027, 1, 1)
_PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}
_PROMO_MODELS = {"gemini-3.8-flash", "gemini-3.7-flash"}


def price_for(model: str, today: date | None = None) -> tuple[float, float] | None:
    if model in _PROMO_MODELS:
        return (0.75, 3.75) if (today or date.today()) < _PROMO_END else (1.50, 7.50)
    return _PRICES.get(model)


def estimate_cost(calls: Sequence[CallRecord], today: date | None = None) -> float | None:
    """Estimated USD cost, or None if any model used has no known price."""
    total = 0.0
    for c in calls:
        price = price_for(c.model, today)
        if price is None:
            return None
        total += (
            c.prompt_tokens * price[0] + (c.output_tokens + c.thought_tokens) * price[1]
        ) / 1e6
    return total
