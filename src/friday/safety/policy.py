"""The safety gate in front of every tool call: allow it, ask the user first, or refuse it.

Friday is autonomous: reads, new files and ordinary commands just run. Only actions that delete,
stop, overwrite or change the system ask first, and a short list is always refused. The rules
themselves are in rules.toml. This is a pattern-based gate, not a sandbox.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from friday.desktop.input import normalize as normalize_keys
from friday.safety.audit import AuditLog
from friday.tools.files import resolve_path
from friday.tools.registry import Decision, Risk, Tool

_RULES_FILE = Path(__file__).with_name("rules.toml")

Level = Literal["allow", "ask", "deny"]


@dataclass(frozen=True, slots=True)
class Assessment:
    level: Level
    summary: str  # what the action does, shown to the user
    reason: str = ""  # why it asks or is refused


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    tool: str
    summary: str
    reason: str


Confirm = Callable[[ApprovalRequest], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class Rules:
    secret_paths: re.Pattern[str]
    secret_mentions: re.Pattern[str]
    forbidden_commands: re.Pattern[str]
    risky_commands: re.Pattern[str]
    risky_clicks: re.Pattern[str]
    risky_keys: frozenset[str]
    protected_folders: tuple[Path, ...]

    @classmethod
    def load(cls, path: Path = _RULES_FILE) -> Rules:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))

        def pattern(name: str) -> re.Pattern[str]:
            return re.compile("|".join(f"(?:{p})" for p in data[name]), re.IGNORECASE)

        folders = (os.environ.get(var) for var in data["protected_folders"])
        return cls(
            secret_paths=pattern("secret_paths"),
            secret_mentions=pattern("secret_mentions"),
            forbidden_commands=pattern("forbidden_commands"),
            risky_commands=pattern("risky_commands"),
            risky_clicks=pattern("risky_clicks"),
            risky_keys=frozenset(data["risky_keys"]),
            protected_folders=tuple(Path(f).resolve(strict=False) for f in folders if f),
        )


class SafetyPolicy:
    """The tool registry's approver. `confirm` asks the user; without it, asking means no."""

    def __init__(
        self,
        confirm: Confirm | None = None,
        *,
        audit: AuditLog | None = None,
        describe_element: Callable[[int], str | None] | None = None,
        describe_web_element: Callable[[int], str | None] | None = None,
        rules: Rules | None = None,
    ) -> None:
        self._confirm = confirm
        self._audit = audit
        self._describe_element = describe_element
        self._describe_web_element = describe_web_element
        self._rules = rules or Rules.load()

    async def __call__(self, tool: Tool, args: BaseModel) -> Decision:
        result = self.assess(tool.name, args)
        if result.level == "allow":
            if tool.risk is not Risk.READ:
                self._log(tool.name, result, "auto")
            return Decision(True)
        if result.level == "deny":
            self._log(tool.name, result, "refused")
            return Decision(
                False, f"the safety policy refuses this ({result.reason}). Tell the user."
            )
        if self._confirm is None:
            self._log(tool.name, result, "refused (nobody to ask)")
            return Decision(False, "this needs the user's approval, but nobody can approve it.")
        approved = await self._confirm(ApprovalRequest(tool.name, result.summary, result.reason))
        self._log(tool.name, result, "approved" if approved else "declined")
        if approved:
            return Decision(True)
        return Decision(False, "the user declined. Do not retry or work around it; say so.")

    def assess(self, tool: str, args: Any) -> Assessment:
        match tool:
            case "run_command":
                return self._command(args.command, args.cwd)
            case "read_file":
                return self._read(resolve_path(args.path))
            case "write_file":
                return self._write(resolve_path(args.path), args.mode, len(args.content))
            case "delete_path":
                return self._delete(resolve_path(args.path))
            case "click":
                return self._click(args.element, args.x, args.y)
            case "browser_click":
                return self._web_click(args.element)
            case "window_action":
                return self._window_action(args.action, args.window)
            case "press_keys":
                return self._keys(args.keys)
            case _:
                return Assessment("allow", tool)

    # ---- one rule per kind of action ---------------------------------------------------------

    def _command(self, command: str, cwd: str | None) -> Assessment:
        text = command.strip()
        summary = text if len(text) <= 300 else text[:300] + "..."
        if self._rules.secret_mentions.search(text):
            return Assessment("deny", summary, "the command touches credential or secret files")
        if self._rules.forbidden_commands.search(text):
            return Assessment("deny", summary, "this command is on the never-run list")
        if cwd is not None and self._is_protected(resolve_path(cwd)):
            return Assessment("deny", summary, "the working folder is a protected system folder")
        if self._rules.risky_commands.search(text):
            return Assessment("ask", summary, "it can delete, stop or change the system")
        return Assessment("allow", summary)

    def _read(self, path: Path) -> Assessment:
        if self._rules.secret_paths.search(str(path)):
            return Assessment("deny", f"read {path}", "it looks like a credential or secret file")
        return Assessment("allow", f"read {path}")

    def _write(self, path: Path, mode: str, chars: int) -> Assessment:
        summary = f"{mode} {path} ({chars} chars)"
        if self._rules.secret_paths.search(str(path)):
            return Assessment("deny", summary, "it looks like a credential or secret file")
        if self._is_protected(path):
            return Assessment("deny", summary, "it is a protected system location")
        if mode == "overwrite" and path.exists():
            return Assessment("ask", summary, "this replaces an existing file")
        return Assessment("allow", summary)

    def _delete(self, path: Path) -> Assessment:
        summary = f"move to Recycle Bin: {path}"
        if self._is_protected(path) or self._rules.secret_paths.search(str(path)):
            return Assessment("deny", summary, "it is a protected or secret location")
        return Assessment("ask", summary, "it deletes your data")

    def _click(self, element: int | None, x: int | None, y: int | None) -> Assessment:
        name = self._describe_element(element) if element and self._describe_element else None
        target = f"'{name}'" if name else (f"[{element}]" if element else f"({x}, {y})")
        if name and self._rules.risky_clicks.search(name):
            return Assessment("ask", f"click {target}", "it can delete, discard, buy or send")
        return Assessment("allow", f"click {target}")

    def _web_click(self, element: int) -> Assessment:
        name = self._describe_web_element(element) if self._describe_web_element else None
        target = f"'{name}' on a web page" if name else f"[{element}] on a web page"
        if name and self._rules.risky_clicks.search(name):
            return Assessment("ask", f"click {target}", "it can buy, pay, send or delete")
        return Assessment("allow", f"click {target}")

    def _window_action(self, action: str, window: str) -> Assessment:
        if action == "close":
            return Assessment("ask", f"close window '{window}'", "unsaved work could be lost")
        return Assessment("allow", f"{action} '{window}'")

    def _keys(self, keys: str) -> Assessment:
        if any(combo in self._rules.risky_keys for combo in normalize_keys(keys)):
            return Assessment("ask", f"press {keys}", "it closes a window or deletes for good")
        return Assessment("allow", f"press {keys}")

    def _is_protected(self, path: Path) -> bool:
        roots: Sequence[Path] = self._rules.protected_folders
        is_drive_root = path == Path(path.anchor)
        return is_drive_root or any(path == r or r in path.parents for r in roots)

    def _log(self, tool: str, result: Assessment, decision: str) -> None:
        if self._audit is not None:
            self._audit.record(
                tool=tool, summary=result.summary, level=result.level, reason=result.reason,
                decision=decision,
            )  # fmt: skip
