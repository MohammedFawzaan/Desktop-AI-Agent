"""Audit log: one JSON line per action that went through a safety decision (runs/audit.jsonl)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from friday.monitoring.events import Event
from friday.monitoring.tracing import JsonlSink


class AuditLog:
    """Append-only JSONL record of every action that needed a safety decision."""

    def __init__(self, path: Path) -> None:
        self._sink = JsonlSink(path)

    def record(self, **fields: Any) -> None:
        self._sink.emit(Event("audit", fields))
