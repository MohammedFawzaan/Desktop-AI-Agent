"""Live run events (plan, tool calls, verdicts...) and the sinks that receive them."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Event:
    kind: str
    data: dict[str, Any] = field(default_factory=dict[str, Any])
    ts: float = field(default_factory=time.time)


class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...


class NullSink:
    def emit(self, event: Event) -> None:
        pass


class CompositeSink:
    def __init__(self, sinks: Sequence[EventSink]) -> None:
        self._sinks = list(sinks)

    def emit(self, event: Event) -> None:
        for sink in self._sinks:
            sink.emit(event)
