from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class AuditLog:
    """Append-only JSONL record of every action that needed a safety decision."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path

    def record(self, **fields: Any) -> None:
        line = json.dumps({"ts": time.time(), **fields}, default=str)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
