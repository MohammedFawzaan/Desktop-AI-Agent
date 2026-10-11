"""Asking the user before risky actions, and remembering the ones they "always" allow."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

Answer = Literal["yes", "no", "always"]


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    tool: str
    summary: str
    reason: str
    dangerous: bool


Confirm = Callable[[ApprovalRequest], Awaitable[Answer]]


class Approvals:
    """Wraps the `confirm` prompt. Without one, nobody can approve, so the answer is always no."""

    def __init__(self, confirm: Confirm | None) -> None:
        self._confirm = confirm
        self._always: set[str] = set()

    @property
    def available(self) -> bool:
        return self._confirm is not None

    def remembered(self, key: str) -> bool:
        return bool(key) and key in self._always

    async def ask(self, request: ApprovalRequest, *, key: str = "") -> Answer:
        """Ask the user. "always" is remembered only when there is a `key` to remember it by."""
        if self._confirm is None:
            return "no"
        answer = await self._confirm(request)
        if answer == "always" and key:
            self._always.add(key)
        return answer
