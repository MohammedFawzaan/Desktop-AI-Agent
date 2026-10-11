"""The `friday` command (also `python -m friday`): text chat by default, or voice mode.

Text chat lives in `friday.cli`, voice mode in `friday.voice`; this file only routes to them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

import typer

app = typer.Typer(add_completion=False, invoke_without_command=True)


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
    if ctx.invoked_subcommand is None:
        from friday.cli.chat import start_chat

        start_chat(verbose=verbose, trace=trace)


@app.command()
def run(
    ctx: typer.Context,
    goal: Annotated[str, typer.Argument(help="What you want done.")],
) -> None:
    """Run a single task and exit."""
    from friday.cli.chat import run_task

    opts = cast(Options, ctx.obj)
    ok = run_task(goal, verbose=opts.verbose, trace=opts.trace)
    raise typer.Exit(0 if ok else 1)


@app.command()
def voice(
    ctx: typer.Context,
    wake: Annotated[bool, typer.Option(help="Listen for 'Hey Friday'.")] = True,
    hotkey: Annotated[
        str | None, typer.Option(help="Global hotkey, e.g. ctrl+alt+f. Default from settings.")
    ] = None,
) -> None:
    """Talk to Friday: say "Hey Friday" or press the hotkey, then speak."""
    from friday.voice.chat import start_voice_chat

    opts = cast(Options, ctx.obj)
    start_voice_chat(verbose=opts.verbose, trace=opts.trace, wake=wake, hotkey=hotkey)


@app.command()
def mics(
    seconds: Annotated[float, typer.Option(help="How long to listen.")] = 4.0,
    device: Annotated[str | None, typer.Option(help="Device index or part of its name.")] = None,
) -> None:
    """List microphones and test the one Friday will use: speak when it says so."""
    from friday.voice.chat import check_microphones

    check_microphones(seconds, device)


if __name__ == "__main__":
    app()
