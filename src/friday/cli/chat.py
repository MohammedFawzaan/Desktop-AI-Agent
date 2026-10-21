"""Text chat in the terminal: type a request, Friday works on it and answers."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from rich.panel import Panel

from friday.agent.models import SessionTurn
from friday.app import build_app
from friday.cli.display import ConsoleSink, confirm, console, show_result

_REMEMBERED_TURNS = 6

_WELCOME = (
    "Ask me to do things on this PC. I work on my own and only ask before risky actions "
    "(deleting, stopping processes, changing the system, overwriting files).\n"
    "[dim]Ctrl+C during a task cancels it; Ctrl+C or 'exit' at the prompt quits. "
    "Run `friday voice` to talk to me instead.[/dim]"
)


def start_chat() -> None:
    friday = build_app(confirm=confirm, progress=ConsoleSink())
    console.print(Panel(_WELCOME, title="Friday", border_style="cyan"))
    history: list[SessionTurn] = []
    loop = asyncio.new_event_loop()  # one loop for the whole chat, so Ctrl+C can cancel a task
    try:
        while True:
            try:
                message = console.input("\n[bold cyan]You>[/bold cyan] ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\nBye.")
                break
            if message.lower() in {"exit", "quit"}:
                break
            if not message:
                continue
            task = loop.create_task(friday.agent.run(message, session=history))
            try:
                result = loop.run_until_complete(task)
            except KeyboardInterrupt:
                task.cancel()
                with suppress(asyncio.CancelledError, KeyboardInterrupt):
                    loop.run_until_complete(task)
                console.print("\n[yellow]Task cancelled.[/yellow]")
                continue
            show_result(result)
            history.append(SessionTurn(message, result.status, result.summary[:300]))
            del history[:-_REMEMBERED_TURNS]
    finally:
        with suppress(Exception):
            loop.run_until_complete(friday.aclose())
        loop.close()
