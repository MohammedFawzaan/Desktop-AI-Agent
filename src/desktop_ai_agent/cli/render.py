from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from desktop_ai_agent.agent.state import RunResult
from desktop_ai_agent.obs.metrics import estimate_cost

_STATUS_STYLE = {
    "success": "green",
    "failed": "red",
    "blocked": "yellow",
    "budget_exceeded": "yellow",
}


def _one_line(text: str, limit: int) -> str:
    flat = " | ".join(part.strip() for part in text.splitlines() if part.strip())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _fmt_cost(result: RunResult) -> str:
    cost = estimate_cost(result.calls)
    if cost is None:
        return "n/a (unpriced model)"
    return f"${cost:.4f} (estimate)"


def metrics_dict(result: RunResult) -> dict[str, Any]:
    cost = estimate_cost(result.calls)
    return {
        "run_id": result.run_id,
        "status": result.status,
        "goal": result.goal,
        "elapsed_s": round(result.elapsed_s, 3),
        "llm_calls": result.llm_calls,
        "prompt_tokens": result.usage.prompt_tokens,
        "output_tokens": result.usage.output_tokens,
        "thought_tokens": result.usage.thought_tokens,
        "estimated_cost_usd": cost,
        "retries": result.retries,
        "replans": result.replans,
        "calls": [asdict(c) for c in result.calls],
        "tools": [asdict(t) for t in result.tools],
        "verdicts": [asdict(v) for v in result.verdicts],
    }


def render_result(console: Console, result: RunResult) -> None:
    style = _STATUS_STYLE.get(result.status, "white")
    console.print(f"\n[bold {style}]{result.status.upper()}[/bold {style}]")
    console.print(result.summary or "(no summary)")


def render_metrics(console: Console, result: RunResult, *, verbose: bool = False) -> None:
    u = result.usage
    llm_time = sum(c.latency_s for c in result.calls)
    tool_time = sum(t.duration_s for t in result.tools)
    models: dict[str, int] = defaultdict(int)
    for c in result.calls:
        models[c.model] += 1

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim")
    summary.add_column()
    summary.add_row("Status", f"[{_STATUS_STYLE.get(result.status, 'white')}]{result.status}[/]")
    summary.add_row(
        "Total time", f"{result.elapsed_s:.1f}s (LLM {llm_time:.1f}s, tools {tool_time:.1f}s)"
    )
    summary.add_row("Steps", f"{result.steps_completed}/{len(result.plan_steps)} completed")
    summary.add_row("Retries / replans", f"{result.retries} / {result.replans}")
    summary.add_row("LLM calls", str(result.llm_calls))
    summary.add_row(
        "Tokens",
        f"input {u.prompt_tokens:,}  output {u.output_tokens:,}  thinking {u.thought_tokens:,}  "
        f"total {u.prompt_tokens + u.output_tokens + u.thought_tokens:,}",
    )
    summary.add_row("Models", ", ".join(f"{m} x{n}" for m, n in models.items()) or "-")
    summary.add_row("Est. cost", _fmt_cost(result))
    console.print(Panel(summary, title="Run metrics", border_style="cyan", expand=False))

    if result.calls:
        by_purpose: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        latency: dict[str, float] = defaultdict(float)
        for c in result.calls:
            row = by_purpose[c.purpose]
            row[0] += 1
            row[1] += c.prompt_tokens
            row[2] += c.output_tokens
            row[3] += c.thought_tokens
            latency[c.purpose] += c.latency_s
        table = Table(title="Tokens by phase", box=box.SIMPLE_HEAD)
        for name, justify in (
            ("Phase", "left"),
            ("Calls", "right"),
            ("Input", "right"),
            ("Output", "right"),
            ("Thinking", "right"),
            ("Time", "right"),
        ):
            table.add_column(name, justify=justify)  # type: ignore[arg-type]
        for purpose, (n, i, o, t) in by_purpose.items():
            table.add_row(purpose, str(n), f"{i:,}", f"{o:,}", f"{t:,}", f"{latency[purpose]:.1f}s")
        console.print(table)

    if verbose and result.calls:
        table = Table(title="Every LLM call", box=box.SIMPLE_HEAD)
        for name in ("#", "Phase", "Model", "Input", "Output", "Thinking", "Time"):
            table.add_column(name, justify="left" if name in ("Phase", "Model") else "right")
        for n, c in enumerate(result.calls, 1):
            table.add_row(
                str(n), c.purpose, c.model, f"{c.prompt_tokens:,}", f"{c.output_tokens:,}",
                f"{c.thought_tokens:,}", f"{c.latency_s:.1f}s",
            )  # fmt: skip
        console.print(table)

    if result.tools:
        table = Table(title="Tools called", box=box.SIMPLE_HEAD)
        for name in ("#", "Step", "Tool", "Result", "Time"):
            table.add_column(name, no_wrap=True)
        table.add_column("Arguments", overflow="fold", ratio=1)
        table.add_column("Output", overflow="fold", ratio=2)
        for n, t in enumerate(result.tools, 1):
            ok = "[green]ok[/green]" if t.ok else "[red]failed[/red]"
            args = _one_line(str(t.args), 70 if not verbose else 600)
            out = _one_line(t.output, 90 if not verbose else 600)
            table.add_row(
                str(n), str(t.step), t.tool, ok, f"{t.duration_s * 1000:.0f}ms", args, out
            )
        console.print(table)

    if result.verdicts:
        table = Table(title="Verification", box=box.SIMPLE_HEAD)
        for name in ("Step", "Verdict", "Reason"):
            table.add_column(name, overflow="fold")
        for v in result.verdicts:
            label = v.status + (" (auto, read-only)" if v.auto else "")
            color = "green" if v.status == "passed" else "yellow"
            table.add_row(str(v.step), f"[{color}]{label}[/{color}]", _one_line(v.reason, 120))
        console.print(table)
