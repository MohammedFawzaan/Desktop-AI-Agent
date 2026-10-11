"""Text chat in the terminal: type a request, Friday works on it and answers."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from rich.panel import Panel

from friday.agent.loop import RunResult, SessionTurn
from friday.app import build_app
from friday.cli.display import ConsoleSink, confirm, console, finish

_MAX_SESSION_TURNS = 6


def start_chat(*, verbose: bool, trace: bool) -> None:
    """`friday`: chat until Ctrl+C or `exit`."""
    friday_app = build_app(confirm=confirm, progress=ConsoleSink(), trace=trace)
    agent = friday_app.agent
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
            loop.run_until_complete(friday_app.aclose())
        with suppress(Exception):
            loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


def run_task(goal: str, *, verbose: bool, trace: bool) -> bool:
    """`friday run "<task>"`: run one task and report it. True if it succeeded."""
    friday_app = build_app(confirm=confirm, progress=ConsoleSink(), trace=trace)

    async def run_once() -> RunResult:
        try:
            return await friday_app.agent.run(goal)
        finally:
            await friday_app.aclose()

    result = asyncio.run(run_once())
    finish(friday_app.agent, result, verbose)
    return result.status == "success"
