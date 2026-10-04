from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
from rich.console import Console
from rich.panel import Panel

from desktop_ai_agent.agent import Agent, Budget
from desktop_ai_agent.agent.state import RunResult, SessionTurn
from desktop_ai_agent.cli.render import metrics_dict, render_metrics, render_result
from desktop_ai_agent.config import Settings
from desktop_ai_agent.llm import GeminiLLM
from desktop_ai_agent.obs import CompositeSink, Event, EventSink, JsonlSink
from desktop_ai_agent.safety import ApprovalRequest, AuditLog, SafetyPolicy
from desktop_ai_agent.tools.pc import build_pc_registry

app = typer.Typer(add_completion=False, invoke_without_command=True)
console = Console()

_PHASE_STYLE = {
    "plan": "cyan",
    "act": "blue",
    "observe": "magenta",
    "verify": "yellow",
    "recover": "red",
    "finish": "green",
}
_MAX_SESSION_TURNS = 6


@dataclass(frozen=True)
class Options:
    verbose: bool
    trace: bool


class ConsoleSink:
    """Live progress while the agent works."""

    def emit(self, event: Event) -> None:
        d = event.data
        match event.kind:
            case "phase":
                style = _PHASE_STYLE.get(d["phase"], "white")
                console.print(f"[{style}]>> {d['phase'].upper()}[/{style}]")
            case "plan":
                for i, step in enumerate(d["steps"], 1):
                    console.print(f"   {i}. {step['description']}")
            case "tool" | "probe":
                mark = "[green]ok[/green]" if d["ok"] else "[red]fail[/red]"
                label = "check" if event.kind == "probe" else "tool"
                console.print(
                    f"   [dim]{label}: {d['tool']}({_short(d['args'])})[/dim] {mark} "
                    f"{_short(d['output'], 110)}"
                )
            case "verdict":
                console.print(f"   verdict: [bold]{d['status']}[/bold] - {d['reason']}")
            case "recover":
                console.print(f"   recover: {d['decision']}")
            case _:
                pass


def _short(value: object, limit: int = 80) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def _confirm(request: ApprovalRequest) -> Literal["yes", "no", "always"]:
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


def _build_agent(*, trace: bool) -> Agent:
    settings = Settings()  # type: ignore[call-arg]
    llm = GeminiLLM(
        settings.google_gemini_api_key.get_secret_value(),
        settings.agent_model,
        fallback_models=settings.agent_fallback_models,
    )
    sinks: list[EventSink] = [ConsoleSink()]
    if trace:
        sinks.append(JsonlSink(Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl"))
    policy = SafetyPolicy(_confirm, audit=AuditLog(Path("runs") / "audit.jsonl"))
    return Agent(
        llm,
        build_pc_registry(policy),
        budget=Budget(),
        sink=CompositeSink(sinks),
    )


def _finish(agent: Agent, result: RunResult, verbose: bool) -> None:
    render_result(console, result)
    render_metrics(console, result, verbose=verbose)
    agent.emit_raw("metrics", metrics_dict(result))


@app.callback()
def chat(
    ctx: typer.Context,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Full metrics detail.")] = False,
    trace: Annotated[bool, typer.Option(help="Write a JSONL trace to runs/.")] = True,
) -> None:
    """Friday: chat with an agent that works on your PC. Ctrl+C to exit."""
    ctx.obj = Options(verbose=verbose, trace=trace)
    if ctx.invoked_subcommand is not None:
        return

    agent = _build_agent(trace=trace)
    console.print(
        Panel(
            "Ask me to do things on this PC. Reads are free; writes inside Desktop, Documents "
            "and Downloads are automatic; everything else asks you first.\n"
            "[dim]Ctrl+C during a task cancels it; Ctrl+C at the prompt exits.[/dim]",
            title="Friday",
            border_style="cyan",
        )
    )
    history: list[SessionTurn] = []
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                message = console.input("\n[bold cyan]You>[/bold cyan] ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\nBye.")
                break
            if not message:
                continue
            if message.lower() in {"exit", "quit"}:
                break
            task = loop.create_task(agent.run(message, session=history))
            try:
                result = loop.run_until_complete(task)
            except KeyboardInterrupt:
                task.cancel()
                with suppress(asyncio.CancelledError, KeyboardInterrupt):
                    loop.run_until_complete(task)
                console.print("\n[yellow]Task cancelled.[/yellow]")
                continue
            _finish(agent, result, verbose)
            history.append(SessionTurn(message, result.status, result.summary[:300]))
            del history[:-_MAX_SESSION_TURNS]
    finally:
        with suppress(Exception):
            loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


@app.command()
def run(
    ctx: typer.Context,
    goal: Annotated[str, typer.Argument(help="What you want done.")],
) -> None:
    """Run a single task and exit."""
    opts = cast(Options, ctx.obj)
    agent = _build_agent(trace=opts.trace)
    result = asyncio.run(agent.run(goal))
    _finish(agent, result, opts.verbose)
    raise typer.Exit(0 if result.status == "success" else 1)
