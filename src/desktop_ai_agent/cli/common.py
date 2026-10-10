"""Pieces shared by the text chat and the voice mode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.panel import Panel

from desktop_ai_agent.agent import Agent, Budget
from desktop_ai_agent.agent.state import RunResult
from desktop_ai_agent.browser import BrowserController
from desktop_ai_agent.cli.render import metrics_dict, render_metrics, render_result
from desktop_ai_agent.config import Settings
from desktop_ai_agent.gui import GuiController
from desktop_ai_agent.llm import GeminiLLM
from desktop_ai_agent.obs import CompositeSink, Event, EventSink, JsonlSink
from desktop_ai_agent.safety import ApprovalRequest, AuditLog, SafetyPolicy
from desktop_ai_agent.tools.browser import register_browser_tools
from desktop_ai_agent.tools.gui import register_gui_tools
from desktop_ai_agent.tools.pc import build_pc_registry

console = Console()

_PHASE_STYLE = {
    "plan": "cyan",
    "act": "blue",
    "observe": "magenta",
    "verify": "yellow",
    "recover": "red",
    "finish": "green",
}


def short(value: object, limit: int = 80) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class ConsoleSink:
    """Live progress while the agent works."""

    def emit(self, event: Event) -> None:
        d = event.data
        match event.kind:
            case "phase":
                style = _PHASE_STYLE.get(d["phase"], "white")
                console.print(f"[{style}]>> {d['phase'].upper()}[/{style}]")
            case "thinking":
                console.print("[dim]thinking...[/dim]")
            case "grounding":
                console.print("[dim]this needs current information; looking it up...[/dim]")
            case "plan":
                for i, step in enumerate(d["steps"], 1):
                    console.print(f"   {i}. {step['description']}")
            case "tool" | "probe":
                mark = "[green]ok[/green]" if d["ok"] else "[red]fail[/red]"
                label = "check" if event.kind == "probe" else "tool"
                console.print(
                    f"   [dim]{label}: {d['tool']}({short(d['args'])})[/dim] {mark} "
                    f"{short(d['output'], 110)}"
                )
            case "verdict":
                console.print(f"   verdict: [bold]{d['status']}[/bold] - {d['reason']}")
            case "recover":
                console.print(f"   recover: {d['decision']}")
            case _:
                pass


async def confirm(request: ApprovalRequest) -> Literal["yes", "no", "always"]:
    # Blocks the event loop on purpose: nothing else runs while the human decides.
    style = "red" if request.dangerous else "yellow"
    body = request.summary + (f"\n[dim]{request.reason}[/dim]" if request.reason else "")
    console.print(Panel(body, title=f"Approval needed: {request.tool}", border_style=style))
    options = (
        "y = yes, n = no" if request.dangerous else "y = yes, n = no, a = always for this kind"
    )
    while True:
        answer = console.input(f"[bold]Allow?[/bold] ({options}) ").strip().lower()
        if answer in {"y", "yes"}:
            return "yes"
        if answer in {"n", "no", ""}:
            return "no"
        if answer in {"a", "always"} and not request.dangerous:
            return "always"


@dataclass(frozen=True, slots=True)
class Built:
    agent: Agent
    llm: GeminiLLM
    settings: Settings
    gui: GuiController | None = None
    browser: BrowserController | None = None

    async def aclose(self) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self.gui is not None:
            self.gui.close()


def build_agent(*, trace: bool) -> Built:
    settings = Settings()  # type: ignore[call-arg]
    llm = GeminiLLM(
        settings.google_gemini_api_key.get_secret_value(),
        settings.agent_model,
        fallback_models=settings.agent_fallback_models,
        stt_models=settings.stt_models,
        tts_models=settings.tts_models,
        tts_voice=settings.tts_voice,
    )
    sinks: list[EventSink] = [ConsoleSink()]
    if trace:
        sinks.append(JsonlSink(Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl"))
    gui = GuiController() if settings.gui_enabled else None
    browser = (
        BrowserController(
            settings.friday_data_dir / "browser",
            channels=settings.browser_channels,
            headless=settings.browser_headless,
        )
        if settings.browser_enabled
        else None
    )
    policy = SafetyPolicy(
        confirm,
        audit=AuditLog(Path("runs") / "audit.jsonl"),
        describe_element=gui.describe_element if gui else None,
        describe_web_element=browser.describe_element if browser else None,
    )
    registry = build_pc_registry(policy)
    if gui is not None:
        register_gui_tools(registry, gui, llm)
    if browser is not None:
        register_browser_tools(registry, browser)
    agent = Agent(llm, registry, budget=Budget(), sink=CompositeSink(sinks))
    return Built(agent, llm, settings, gui, browser)


def finish(agent: Agent, result: RunResult, verbose: bool) -> None:
    render_result(console, result)
    render_metrics(console, result, verbose=verbose)
    agent.emit_raw("metrics", metrics_dict(result))
