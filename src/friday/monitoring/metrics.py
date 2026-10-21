"""What each model call cost: tokens and time per call, and the estimated price of a run."""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_PRICING_FILE = Path(__file__).with_name("pricing.toml")


@dataclass(frozen=True, slots=True)
class CallRecord:
    purpose: str  # plan, act, verify, search, vision...
    model: str
    prompt_tokens: int
    output_tokens: int
    thought_tokens: int
    latency_s: float


@cache
def prices() -> dict[str, tuple[float, float]]:
    """USD per 1M tokens (input, output) for each known model, from pricing.toml."""
    data = tomllib.loads(_PRICING_FILE.read_text(encoding="utf-8"))
    return {model: (float(i), float(o)) for model, (i, o) in data["models"].items()}


def estimate_cost(calls: Sequence[CallRecord]) -> float | None:
    """Estimated USD cost, or None if a model used has no known price."""
    total = 0.0
    for call in calls:
        price = prices().get(call.model)
        if price is None:
            return None
        billed_output = call.output_tokens + call.thought_tokens
        total += (call.prompt_tokens * price[0] + billed_output * price[1]) / 1e6
    return total
