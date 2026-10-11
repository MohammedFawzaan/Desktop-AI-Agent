"""Decides whether an action runs automatically, needs the user's approval, or is refused.

The agent is autonomous: reads, new files, appends and ordinary commands all run without asking.
Only destructive actions interrupt the user (deletes, killing processes, registry/service/system
changes, overwriting an existing file), and a short never-run list is refused outright. This is a
heuristic gate in front of every tool call, not a sandbox.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from friday.desktop.input import normalize as normalize_keys
from friday.safety.approvals import ApprovalRequest, Approvals, Confirm
from friday.safety.audit import AuditLog
from friday.tools.files import resolve_path
from friday.tools.registry import Decision, Risk, Tool

Level = Literal["allow", "ask", "danger", "deny"]


@dataclass(frozen=True, slots=True)
class Assessment:
    level: Level
    summary: str
    reason: str = ""
    key: str = ""  # what "always allow" remembers (only for level "ask")


_SECRET_PATH = re.compile(
    r"(^|[\\/])\.env(\..*)?$|[\\/]\.ssh([\\/]|$)|id_(rsa|ed25519|ecdsa|dsa)"
    r"|\.(pem|key|pfx|p12|kdbx)$|[\\/]\.aws[\\/]credentials|\.git-credentials|\.npmrc$"
    r"|\.pypirc$|\.netrc$|[\\/]login data$|[\\/]cookies$",
    re.IGNORECASE,
)
_SECRET_IN_TEXT = re.compile(
    r"\.env\b|\.ssh\b|id_(rsa|ed25519|ecdsa|dsa)|\.(pem|key|pfx|p12|kdbx)\b"
    r"|\.aws[\\/]credentials|\.git-credentials|\.npmrc|\.pypirc|\.netrc|login data",
    re.IGNORECASE,
)
_DENY_COMMAND = re.compile(
    r"format-volume|\bformat\s+[a-z]:|\bdiskpart\b|\bbcdedit\b|\bcipher\s+/w|\bvssadmin\b"
    r"|\breg(\.exe)?\s+delete\s+hk(lm|cr)|set-executionpolicy|\bnet\s+(user|localgroup)\b"
    r"|\b(iex|invoke-expression)\b.*\b(iwr|irm|curl|wget|invoke-webrequest|invoke-restmethod"
    r"|downloadstring)\b|\b(iwr|irm|curl|wget|invoke-webrequest|invoke-restmethod)\b.*\|\s*"
    r"(iex|invoke-expression)\b|\brm\s+-\w*r\w*f?\w*\s+[/\\]\s*$",
    re.IGNORECASE,
)
_DANGER_COMMAND = re.compile(
    r"\bremove-item\b|\bdel\b|\berase\b|\brd\b|\brmdir\b|\brm\b|\bshutdown\b"
    r"|restart-computer|stop-computer|stop-process|\btaskkill\b|\bkill\b|\breg(\.exe)?\b|regedit"
    r"|set-itemproperty|new-itemproperty|remove-itemproperty|\bnetsh\b|\bsc(\.exe)?\b|schtasks"
    r"|\bicacls\b|\btakeown\b|\bcacls\b|clear-(content|recyclebin|eventlog|disk|item)"
    r"|uninstall|\bdisable-\w+|\bunregister-\w+|\bset-service\b|\bstop-service\b",
    re.IGNORECASE,
)

_DANGER_CLICK = re.compile(
    r"\b(delete|remove|uninstall|erase|format|discard|wipe|reset|permanently|factory)\b"
    r"|don'?t save|do not save|empty recycle bin|sign out|log out"
    r"|\b(buy|pay|purchase|checkout|place order|order now|confirm order|confirm payment)\b"
    r"|\b(transfer|send money|subscribe|unsubscribe)\b",
    re.IGNORECASE,
)
_CLOSE_KEYS = {"alt+f4", "ctrl+w", "ctrl+shift+w"}
_DANGER_KEYS = {"shift+delete", "shift+del"}


def _protected_roots() -> list[Path]:
    roots: list[Path] = []
    for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        value = os.environ.get(var)
        if value:
            roots.append(Path(value).resolve(strict=False))
    return roots


def _is_under(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def _is_drive_root(path: Path) -> bool:
    return path == Path(path.anchor)


def assess_command(command: str) -> Assessment:
    text = command.strip()
    summary = text if len(text) <= 300 else text[:300] + "..."
    if _SECRET_IN_TEXT.search(text):
        return Assessment("deny", summary, "the command touches credential or secret files")
    if _DENY_COMMAND.search(text):
        return Assessment("deny", summary, "this command is on the never-run list")
    if _DANGER_COMMAND.search(text):
        return Assessment("danger", summary, "the command can delete, stop or change the system")
    return Assessment("allow", summary)


class SafetyPolicy:
    """Approver for the tool registry. `confirm` asks the human; without it, asks become denials."""

    def __init__(
        self,
        confirm: Confirm | None = None,
        *,
        audit: AuditLog | None = None,
        describe_element: Callable[[int], str | None] | None = None,
        describe_web_element: Callable[[int], str | None] | None = None,
    ) -> None:
        self._describe = describe_element
        self._describe_web = describe_web_element
        self._protected = _protected_roots()
        self._approvals = Approvals(confirm)
        self._audit = audit

    # ---- assessment (pure) ------------------------------------------------------------------

    def assess(self, tool: str, args: BaseModel | Any) -> Assessment:
        a: Any = args
        if tool == "run_command":
            base = assess_command(a.command)
            if base.level != "deny" and a.cwd is not None:
                cwd = resolve_path(a.cwd)
                if _is_under(cwd, self._protected):
                    return Assessment("deny", base.summary, f"working folder {cwd} is protected")
            return base
        if tool == "read_file":
            path = resolve_path(a.path)
            if _SECRET_PATH.search(str(path)):
                return Assessment(
                    "deny", f"read {path}", "it looks like a credential or secret file"
                )
            return Assessment("allow", f"read {path}")
        if tool == "write_file":
            return self._assess_write(a)
        if tool == "delete_path":
            path = resolve_path(a.path)
            if _is_under(path, self._protected) or _is_drive_root(path):
                return Assessment("deny", f"delete {path}", "it is a protected system location")
            if _SECRET_PATH.search(str(path)):
                return Assessment("deny", f"delete {path}", "it looks like a credential file")
            return Assessment("danger", f"move to Recycle Bin: {path}", "deleting user data")
        if tool in ("click", "window_action", "press_keys"):
            return self._assess_gui(tool, a)
        if tool == "browser_click":
            name = self._describe_web(a.element) if self._describe_web else None
            if name and _DANGER_CLICK.search(name):
                return Assessment(
                    "danger", f"click '{name}' on a web page", "it can buy, pay, send or delete"
                )
            return Assessment("allow", f"click {name or a.element} on a web page")
        return Assessment("allow", tool)

    def _assess_gui(self, tool: str, a: Any) -> Assessment:
        if tool == "click":
            name = self._describe(a.element) if a.element is not None and self._describe else None
            target = f"'{name}'" if name else (f"[{a.element}]" if a.element else f"({a.x}, {a.y})")
            if name and _DANGER_CLICK.search(name):
                return Assessment("danger", f"click {target}", "it can delete or discard data")
            return Assessment("allow", f"click {target}")
        if tool == "window_action":
            if a.action == "close":
                return Assessment(
                    "ask", f"close window '{a.window}'", "unsaved work in it could be lost",
                    f"close:{a.window.lower()}",
                )  # fmt: skip
            return Assessment("allow", f"{a.action} '{a.window}'")
        combos = normalize_keys(a.keys)
        if any(c in _DANGER_KEYS for c in combos):
            return Assessment("danger", f"press {a.keys}", "it deletes without the Recycle Bin")
        closing = [c for c in combos if c in _CLOSE_KEYS]
        if closing:
            return Assessment(
                "ask", f"press {a.keys}", "it closes a window or tab; unsaved work could be lost",
                f"keys:{closing[0]}",
            )  # fmt: skip
        return Assessment("allow", f"press {a.keys}")

    def _assess_write(self, a: Any) -> Assessment:
        path = resolve_path(a.path)
        summary = f"{a.mode} {path} ({len(a.content)} chars)"
        if _SECRET_PATH.search(str(path)):
            return Assessment("deny", summary, "it looks like a credential or secret file")
        if _is_under(path, self._protected) or _is_drive_root(path):
            return Assessment("deny", summary, "it is a protected system location")
        if a.mode == "overwrite" and path.exists():
            return Assessment("ask", summary, "this replaces an existing file", f"overwrite:{path}")
        return Assessment("allow", summary)

    # ---- Approver ---------------------------------------------------------------------------

    async def __call__(self, tool: Tool, args: BaseModel) -> Decision:
        result = self.assess(tool.name, args)
        if result.level == "allow":
            if tool.risk is not Risk.READ:
                self._log(tool.name, result, "auto")
            return Decision(True)
        if result.level == "deny":
            self._log(tool.name, result, "blocked")
            return Decision(
                False,
                f"safety policy refuses this ({result.reason}). Do not try a workaround; "
                "tell the user.",
            )
        if result.level == "ask" and self._approvals.remembered(result.key):
            self._log(tool.name, result, "auto (always)")
            return Decision(True)
        if not self._approvals.available:
            self._log(tool.name, result, "blocked (no interactive approval)")
            return Decision(False, "this action needs the user's approval but none is available.")
        dangerous = result.level == "danger"
        answer = await self._approvals.ask(
            ApprovalRequest(tool.name, result.summary, result.reason, dangerous),
            key="" if dangerous else result.key,
        )
        if answer == "no":
            self._log(tool.name, result, "declined")
            return Decision(
                False,
                "the user declined this action. Do not retry it or work around it; "
                "report that it was declined.",
            )
        self._log(tool.name, result, "approved")
        return Decision(True)

    def _log(self, tool: str, result: Assessment, decision: str) -> None:
        if self._audit:
            self._audit.record(
                tool=tool,
                summary=result.summary,
                level=result.level,
                reason=result.reason,
                decision=decision,
            )
