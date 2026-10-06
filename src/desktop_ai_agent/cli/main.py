from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Annotated, cast

import typer
from rich.panel import Panel

from desktop_ai_agent.agent.state import SessionTurn
from desktop_ai_agent.cli.common import build_agent, console, finish

app = typer.Typer(add_completion=False, invoke_without_command=True)

_MAX_SESSION_TURNS = 6


@dataclass(frozen=True)
class Options:
    verbose: bool
    trace: bool


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

    agent = build_agent(trace=trace).agent
    console.print(
        Panel(
            "Ask me to do things on this PC. I work autonomously; I only ask before destructive "
            "actions (deletes, stopping processes, registry/system changes, overwriting files).\n"
            "[dim]Ctrl+C during a task cancels it; Ctrl+C at the prompt exits. "
            "Try `friday voice` to talk to me.[/dim]",
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
            finish(agent, result, verbose)
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
    agent = build_agent(trace=opts.trace).agent
    result = asyncio.run(agent.run(goal))
    finish(agent, result, opts.verbose)
    raise typer.Exit(0 if result.status == "success" else 1)


@app.command()
def mics(
    seconds: Annotated[float, typer.Option(help="How long to listen.")] = 4.0,
    device: Annotated[str | None, typer.Option(help="Device index or part of its name.")] = None,
) -> None:
    """List microphones and test the one Friday will use: speak when it says so."""
    from desktop_ai_agent.voice.mic import list_input_devices, measure_level

    for d in list_input_devices():
        star = "*" if d.is_default else " "
        console.print(f"{star} [{d.index:>2}] {d.host:<12} {d.name} ({d.channels} ch)")
    spec: int | str | None = int(device) if device and device.isdigit() else device
    console.print(f"\n[bold]Speak normally for {seconds:g} seconds...[/bold]")
    level, peak = measure_level(spec, seconds)
    console.print(
        f"Average level {level:.0f}, loudest {peak} (silence is 0-2; speech is usually 1000+)."
    )
    if peak < 50:
        console.print(
            "[red]No sound picked up.[/red] Check Windows Settings > Privacy > Microphone, the "
            "mic mute switch, and the input device in Sound settings; or pass --device."
        )
    elif peak < 1000:
        console.print("[yellow]Quiet. It works, but raise the input volume in Windows.[/yellow]")
    else:
        console.print("[green]The microphone is working.[/green]")


@app.command()
def voice(
    ctx: typer.Context,
    wake: Annotated[bool, typer.Option(help="Listen for 'Hey Friday'.")] = True,
    hotkey: Annotated[
        str | None, typer.Option(help="Global hotkey, e.g. ctrl+alt+f. Default from settings.")
    ] = None,
) -> None:
    """Talk to Friday: say "Hey Friday" or press the hotkey, then speak."""
    from desktop_ai_agent.cli.voice import run_voice

    opts = cast(Options, ctx.obj)
    built = build_agent(trace=opts.trace)
    spec = hotkey or built.settings.friday_hotkey
    try:
        asyncio.run(run_voice(built, wake=wake, hotkey_spec=spec, verbose=opts.verbose))
    except KeyboardInterrupt:
        console.print("\nBye.")
