"""What the terminal shows: live progress, approval prompts, and each answer with its metrics."""

from __future__ import annotations

import io
import sys
from collections import Counter

from rich.console import Console
from rich.panel import Panel

from friday.agent.models import RunResult
from friday.monitoring import Event, estimate_cost
from friday.safety import ApprovalRequest

# Never crash on a character the terminal cannot show (e.g. the rupee sign in an old console).
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(errors="replace")  # pyright: ignore[reportUnknownMemberType]
console = Console()

_PHASE_COLORS = {"act": "blue", "observe": "magenta", "verify": "yellow", "recover": "red"}
_STATUS_COLORS = {"success": "green", "failed": "red", "blocked": "yellow"}


def one_line(value: object, limit: int = 100) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class ConsoleSink:
    """Prints the agent's progress as it works."""

    def emit(self, event: Event) -> None:
        d = event.data
        match event.kind:
            case "thinking":
                console.print("[dim]thinking...[/dim]")
            case "search":
                console.print(f"[dim]searching the web: {d['query']}[/dim]")
            case "plan":
                console.print("[cyan]>> PLAN[/cyan]")
                for i, step in enumerate(d["steps"], 1):
                    console.print(f"   {i}. {step}")
            case "phase":
                color = _PHASE_COLORS.get(d["phase"], "green")
                console.print(f"[{color}]>> {d['phase'].upper()}[/{color}]")
            case "tool":
                mark = "[green]ok[/green]" if d["ok"] else "[red]failed[/red]"
                console.print(f"   [dim]{d['tool']}({one_line(d['args'], 80)})[/dim] {mark}")
                console.print(f"   [dim]{one_line(d['output'])}[/dim]")
            case "verdict":
                console.print(f"   verdict: [bold]{d['status']}[/bold] - {d['reason']}")
            case "recover":
                console.print(f"   recover: {d['decision']}")
            case _:
                pass


async def confirm(request: ApprovalRequest) -> bool:
    """Ask the user before a risky action. Blocks on purpose: nothing runs while they decide."""
    body = f"{request.summary}\n[dim]{request.reason}[/dim]"
    console.print(Panel(body, title=f"Allow {request.tool}?", border_style="yellow"))
    answer = console.input("[bold]Allow?[/bold] (y/n) ").strip().lower()
    return answer in {"y", "yes"}


def show_result(result: RunResult) -> None:
    """The answer, then one panel with the run's status, time, model calls, tokens and cost."""
    color = _STATUS_COLORS.get(result.status, "yellow")
    console.print(f"\n[bold {color}]{result.status.upper()}[/bold {color}]")
    console.print(result.summary or "(no answer)")

    usage = result.usage
    cost = estimate_cost(result.calls)
    calls_by_purpose = Counter(c.purpose for c in result.calls)
    lines = [
        f"Time         {result.elapsed_s:.1f}s",
        f"Model calls  {result.llm_calls}  ("
        + ", ".join(f"{purpose} {n}" for purpose, n in calls_by_purpose.items())
        + ")",
        f"Tokens       {usage.prompt_tokens:,} in, {usage.output_tokens:,} out, "
        f"{usage.thought_tokens:,} thinking",
        f"Cost         {'unknown' if cost is None else f'~${cost:.4f}'}",
    ]
    console.print(Panel("\n".join(lines), title="Metrics", border_style="dim", expand=False))
