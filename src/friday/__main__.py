"""The `friday` command (also `python -m friday`): text chat by default, or `friday voice`."""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(add_completion=False, invoke_without_command=True)


@app.callback()
def chat(ctx: typer.Context) -> None:
    """Chat with Friday, an AI agent that works on your PC. Ctrl+C to exit."""
    if ctx.invoked_subcommand is None:
        from friday.cli.chat import start_chat

        start_chat()


@app.command()
def voice(
    wake: Annotated[bool, typer.Option(help="Listen for 'Hey Friday' (else hotkey only).")] = True,
) -> None:
    """Talk to Friday: say "Hey Friday" or press the hotkey, then speak."""
    from friday.voice.chat import start_voice_chat

    start_voice_chat(wake=wake)


if __name__ == "__main__":
    app()
