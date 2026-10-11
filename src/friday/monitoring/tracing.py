"""Run traces: every event of a run written to `runs/<time>.jsonl`, to replay or diff later."""

from __future__ import annotations

import json
from pathlib import Path

from friday.monitoring.events import Event


class JsonlSink:
    """One JSON object per line, so a run can be replayed and diffed later."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path

    def emit(self, event: Event) -> None:
        line = json.dumps({"ts": event.ts, "kind": event.kind, **event.data}, default=str)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
