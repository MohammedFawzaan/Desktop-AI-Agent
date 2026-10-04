# Progress

Rebuild of Desktop AI Agent in Python: an agent (Friday) that acts on the user's Windows PC instantly, smartly, reliably and safely. Focus is the agent harness; CLI only.

## Status

**Milestones 1 and 2 complete.** Friday runs as a chat in the terminal and can work on the real PC: find locations, read/write files, run PowerShell commands, all behind a safety gate, with detailed metrics after every task. GUI control (clicking/typing in apps) is not built yet.

## What was built

**Milestone 1 - harness:** Gemini client, Plan/Act/Observe/Verify/Recover loop, tool registry, events + JSONL traces.

**Milestone 2 - acts on the real PC:**
- **Chat CLI** (`friday`): type a message, the agent works, output and metrics appear, repeat. Keeps the last 6 tasks as context so follow-ups ("now delete it") work. Messages needing no tools (greetings, questions) are answered in a single LLM call; questions that only need lookups (e.g. system specs) are answered straight from the lookup with no verifier call (3 calls).
- **Autonomous discovery, nothing hard-coded:** the agent is told nothing about the PC up front. It calls `get_system_context` (OS, hardware, RAM, drives, user, real Desktop/Documents/Downloads/... locations from the Windows API, time) or `find_paths` / `list_dir` when it needs them.
- **PC tools** (`tools/pc.py`): `get_system_context`, `read_file`, `list_dir`, `find_paths`, `write_file` (create / overwrite / append), `delete_path` (Recycle Bin), `run_command` (non-interactive Windows PowerShell, timeout + process-tree kill).
- **Safety gate** (`safety/`): deterministic policy in front of every tool call.
  - Reads and known read-only commands: automatic.
  - Writes inside Desktop / Documents / Downloads (new files or appends): automatic.
  - Overwrites, writes elsewhere, and any command not known to be read-only: ask `[y/n/always]`.
  - Delete/stop/registry/uninstall-style commands and deletes: always ask, "always" not offered.
  - Never run / never touch: credential and secret files (`.env`, `.ssh`, keys), Windows and Program Files, disk format, download-and-execute, execution-policy changes.
  - A declined or blocked action tells the agent not to work around it. Every non-read action is logged to `runs/audit.jsonl`.
- **Metrics after every task:** status, time (LLM vs tools), steps, retries/replans, LLM calls, input/output/thinking tokens (per phase, and per call with `-v`), models used (fallbacks visible), tools table (arguments, result, time, output), verification verdicts, estimated cost. The full metrics are also saved in the JSONL trace.

## How the agent works

Plan -> Act -> Observe -> Verify -> Recover -> Continue/Finish. A run ends only on a verified goal, blocked/failed after recovery, or an exhausted budget.

1. **Plan:** fewest steps with observable expected outcomes; or a direct answer if no tools are needed.
2. **Act / Observe:** one step at a time with a fresh small context; tools run in order, failures skip the rest of the turn, pure reads run concurrently.
3. **Verify:** independent verifier judges from tool outputs (can re-check fresh state with read-only tools and submits a verdict in one call). The last step's verdict also judges the whole goal; read-only steps skip verification, and a run that only read things is answered without any verifier call.
4. **Recover:** retry step (max 2) -> replan (max 2) -> give up; `blocked` stops. Repeated identical call + output forces a replan.
5. **Budgets:** 40 LLM calls, 10 turns per step, 15 min.

## Decisions

- **LLM:** Gemini via Google AI Studio, `gemini-3.8-flash` primary, fallbacks `gemini-3.7-flash` -> `gemini-3.5-flash` -> `gemini-3.5-flash-lite`. Key in `.env` as `GOOGLE_GEMINI_API_KEY`; override with `AGENT_MODEL`.
- **Agent loop:** custom-built. LangChain/LangGraph only where clearly warranted.
- **Next for desktop control:** Windows UI Automation first, screenshot vision as fallback.
- **Interface:** CLI only.

## Tech stack

| Area | Tool | Status |
|---|---|---|
| Language | Python 3.13 | in use |
| Env / packages | uv 0.12.21 | in use |
| Lint / format | ruff 0.16.9 | in use |
| Type check | pyright 1.1.414 (strict) | in use |
| Tests | pytest 9.1.1, pytest-asyncio 1.4.0 | in use |
| LLM SDK | google-genai 2.25.0 | in use |
| Schemas / config | pydantic 2.13.5, pydantic-settings 2.15.0 | in use |
| CLI / UI | typer 0.27.2, rich 15.0.0 | in use |
| Retries | tenacity 9.1.4 | in use |
| Recycle Bin deletes | send2trash 2.1.0 | in use |
| Logging | structlog 26.1.0 | installed, not used yet |
| GUI automation | pywinauto, pywin32 | planned |
| Screenshots / images | mss, pillow | planned |

Cost estimates use Google's paid-tier prices (gemini-3.8/3.7 Flash: $0.75 in / $3.75 out per 1M tokens until 2026-12-31, then $1.50 / $7.50; thinking counts as output). Table in `obs/metrics.py`.

## Layout (`src/desktop_ai_agent/`)

`llm/` Gemini client + protocol · `agent/` state machine, prompts, state · `tools/` registry, risk levels, PC tools · `safety/` policy + audit log · `obs/` events, traces, metrics records · `cli/` chat app + metrics rendering · `env.py` runtime system discovery · `config.py` settings. Tests in `tests/` (61, including real PowerShell and file operations).

## How to use

- Chat: `uv run friday` (add `-v` for full metrics detail). Ctrl+C during a task cancels it; Ctrl+C at the prompt (or `exit`) quits.
- One task: `uv run friday run "make a text file on my desktop with a definition of machine learning"`.
- Traces: `runs/<timestamp>.jsonl`; safety audit: `runs/audit.jsonl`.
- Checks: `uv run pytest`, `uv run ruff check .`, `uv run pyright`.

## Milestones

1. [x] Agent harness (Gemini client, loop, tool registry)
2. [x] Acts on the real PC: chat CLI, file + PowerShell tools, autonomous discovery, safety gate, metrics
3. [ ] GUI control (Windows UI Automation + screenshot vision fallback: click, type, windows, apps) plus voice: speech-to-text for commands and text-to-speech for replies (planned, not started)
4. [ ] Eval suite of real tasks; safety hardening (smarter command analysis, per-folder trust settings)

## Known issues / limits

- The safety policy is a heuristic gate, not a sandbox: anything not clearly read-only asks first, but approve commands you read carefully.
- Ctrl+C handling (cancel task, return to prompt) and live approval prompts were tested with piped input, not interactively; check them in a real terminal.
- Approval prompts block the event loop while waiting (nothing else runs during them, by design).
- Fallback models may reject thought signatures from a different model mid-step (untested).
- The SDK prints a harmless AFC warning on each call.
- The Gemini key's project must stay on a paid tier (free tier allows 20 requests/day/model).
