"""Decides whether an action runs automatically, needs the user's approval, or is refused.

This is a deterministic gate in front of every tool call. It is a heuristic layer, not a
sandbox: commands that are not clearly read-only always require the user's approval.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from desktop_ai_agent.env import known_folders
from desktop_ai_agent.safety.audit import AuditLog
from desktop_ai_agent.tools.base import Risk, Tool
from desktop_ai_agent.tools.pc import resolve_path
from desktop_ai_agent.tools.registry import Decision

Level = Literal["allow", "ask", "danger", "deny"]


@dataclass(frozen=True, slots=True)
class Assessment:
    level: Level
    summary: str
    reason: str = ""
    key: str = ""  # what "always allow" remembers


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    tool: str
    summary: str
    reason: str
    dangerous: bool


Confirm = Callable[[ApprovalRequest], Awaitable[Literal["yes", "no", "always"]]]

_SECRET_PATH = re.compile(
    r"(^|[\\/])\.env(\..*)?$|[\\/]\.ssh([\\/]|$)|id_(rsa|ed25519|ecdsa|dsa)|\.(pem|key|pfx|p12|kdbx)$"
    r"|[\\/]\.aws[\\/]credentials|\.git-credentials|\.npmrc$|\.pypirc$|\.netrc$"
    r"|[\\/]login data$|[\\/]cookies$",
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
    r"|\bicacls\b|\btakeown\b|\bcacls\b|\bclear-\w+|\bformat-\w+|move-item|\bmv\b|\bren\b"
    r"|rename-item|uninstall|\bdisable-\w+|\bunregister-\w+|\bset-service\b|\bstop-service\b",
    re.IGNORECASE,
)
_SAFE_COMMANDS = frozenset(
    [
        "get-childitem",
        "gci",
        "dir",
        "ls",
        "get-content",
        "gc",
        "cat",
        "type",
        "get-item",
        "gi",
        "get-location",
        "pwd",
        "whoami",
        "hostname",
        "get-date",
        "get-process",
        "ps",
        "systeminfo",
        "ver",
        "write-output",
        "write-host",
        "select-string",
        "sls",
        "findstr",
        "measure-object",
        "sort-object",
        "select-object",
        "where-object",
        "test-path",
        "resolve-path",
        "split-path",
        "get-filehash",
        "get-command",
        "where.exe",
        "get-psdrive",
        "get-volume",
        "tree",
        "get-host",
        "get-computerinfo",
    ]
)
_SAFE_GIT = frozenset({"status", "log", "diff", "branch", "show", "remote"})
_UNSAFE_CHARS = re.compile(r"[;&><`{}]|\$\(|@\(")


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


def _is_safe_readonly(command: str) -> bool:
    if _UNSAFE_CHARS.search(command):
        return False
    for segment in command.split("|"):
        tokens = segment.strip().split()
        if not tokens:
            return False
        head = tokens[0].lower()
        if head == "git":
            if len(tokens) < 2 or tokens[1].lower() not in _SAFE_GIT:
                return False
        elif head in {"python", "node", "uv", "pip", "npm", "pwsh"}:
            if tokens[1:2] not in (["--version"], ["-v"], ["-version"]):
                return False
        elif head not in _SAFE_COMMANDS:
            return False
    return True


def assess_command(command: str) -> Assessment:
    text = command.strip()
    summary = text if len(text) <= 300 else text[:300] + "..."
    if _SECRET_IN_TEXT.search(text):
        return Assessment("deny", summary, "the command touches credential or secret files")
    if _DENY_COMMAND.search(text):
        return Assessment("deny", summary, "this command is on the never-run list")
    if _DANGER_COMMAND.search(text):
        return Assessment("danger", summary, "the command can delete, stop or change things")
    if _is_safe_readonly(text):
        return Assessment("allow", summary)
    first = text.split()[0].lower() if text.split() else ""
    return Assessment("ask", summary, "the command is not known to be read-only", f"cmd:{first}")


class SafetyPolicy:
    """Approver for the tool registry. `confirm` asks the human; without it, asks become denials."""

    def __init__(
        self,
        confirm: Confirm | None = None,
        *,
        safe_roots: Sequence[Path] | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        folders = known_folders()
        self._safe_roots = [
            p.resolve(strict=False)
            for p in (
                safe_roots or [folders["Desktop"], folders["Documents"], folders["Downloads"]]
            )
        ]
        self._protected = _protected_roots()
        self._confirm = confirm
        self._audit = audit
        self._always: set[str] = set()

    # ---- assessment (pure) ------------------------------------------------------------------

    def assess(self, tool: str, args: BaseModel | Any) -> Assessment:
        a: Any = args
        if tool == "run_command":
            base = assess_command(a.command)
            if base.level == "allow" or a.cwd is None:
                return base
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
        return Assessment("allow", tool)

    def _assess_write(self, a: Any) -> Assessment:
        path = resolve_path(a.path)
        summary = f"{a.mode} {path} ({len(a.content)} chars)"
        if _SECRET_PATH.search(str(path)):
            return Assessment("deny", summary, "it looks like a credential or secret file")
        if _is_under(path, self._protected) or _is_drive_root(path):
            return Assessment("deny", summary, "it is a protected system location")
        overwriting = a.mode == "overwrite" and path.exists()
        if overwriting:
            return Assessment("ask", summary, "this replaces an existing file", f"overwrite:{path}")
        if _is_under(path, self._safe_roots):
            return Assessment("allow", summary)
        return Assessment(
            "ask", summary, "it is outside Desktop/Documents/Downloads", f"write:{path.parent}"
        )

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
        if result.level == "ask" and result.key in self._always:
            self._log(tool.name, result, "auto (always)")
            return Decision(True)
        if self._confirm is None:
            self._log(tool.name, result, "blocked (no interactive approval)")
            return Decision(False, "this action needs the user's approval but none is available.")
        answer = await self._confirm(
            ApprovalRequest(tool.name, result.summary, result.reason, result.level == "danger")
        )
        if answer == "no":
            self._log(tool.name, result, "declined")
            return Decision(
                False,
                "the user declined this action. Do not retry it or work around it; "
                "report that it was declined.",
            )
        if answer == "always" and result.level == "ask" and result.key:
            self._always.add(result.key)
        self._log(tool.name, result, "approved")
        return Decision(True)

    def _log(self, tool: str, result: Assessment, decision: str) -> None:
        if self._audit:
            self._audit.record(
                tool=tool, summary=result.summary, level=result.level,
                reason=result.reason, decision=decision,
            )  # fmt: skip
