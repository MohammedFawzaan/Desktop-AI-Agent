from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
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


class JsonlSink:
    """One JSON object per line, so a run can be replayed and diffed later."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path

    def emit(self, event: Event) -> None:
        line = json.dumps({"ts": event.ts, "kind": event.kind, **event.data}, default=str)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
