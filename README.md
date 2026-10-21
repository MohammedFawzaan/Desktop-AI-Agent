# Friday

An autonomous AI agent for your Windows PC. Tell it what you want in plain English, by typing or by voice ("Hey Friday"), and it plans the work, runs real commands, checks the result, and answers.

> "Make a text file on my desktop with a definition of machine learning."
> Friday finds your real Desktop folder, writes the file, reads it back to verify, and tells you it's done.
>
> "Open Calculator and work out 25 times 4." / "Find who designed Python on Wikipedia."
> Friday operates the app or the website herself and reports the verified result.

Built in Python on Google's Gemini models, with its own agent loop instead of a framework.

## What it does

- **Acts on your real PC.** Reads, writes, lists and searches files; runs PowerShell commands; discovers your folders and hardware on its own (nothing is hard-coded).
- **Uses your apps.** Opens apps by name, reads their buttons and fields, clicks, types and presses shortcuts (Windows UI Automation), with a screenshot + vision fallback.
- **Searches the web.** Questions about anything that can change (news, prices, rates, weather) are answered from a live Google search, with sources. Pages can be read directly without opening a browser.
- **Uses websites.** Has its own browser window (Playwright on your installed Chrome or Edge, separate profile) to open pages, read them, click, fill forms and search.
- **Plans, acts, verifies, recovers.** Every task runs through Plan, Act, Observe, Verify, Recover. A task only counts as done when the result is checked against real system state; failures are retried or re-planned.
- **Answers questions instantly.** Greetings, facts and advice are answered directly in one call (about 2 seconds), with no planning or tools.
- **Voice control.** Say "Hey Friday" or press a global hotkey, then speak. Replies are spoken back, streamed so sound starts about 1.3 seconds after the answer is ready.
- **Safe by default.** Ordinary actions run automatically; destructive ones (deletes, killing processes, registry changes) ask first; secrets and system folders are off limits. Everything is logged.
- **Shows its work.** Live progress while it works, then one metrics panel per answer: time, model calls by phase, tokens and estimated cost. Every run is also traced to `runs/`.

## Requirements

- Windows 10 or 11 (uses PowerShell, WASAPI audio and Windows hotkeys)
- [uv](https://docs.astral.sh/uv/) (installs Python 3.13 for you)
- A Google AI Studio API key (a paid-tier project is recommended; the free tier allows only about 20 requests per day per model)
- For voice: a microphone and speakers

## Setup

```powershell
git clone <your-repo-url> friday
cd friday
uv sync
copy .env.example .env      # then put your key in .env: GOOGLE_GEMINI_API_KEY=...
```

## Usage

| Command | What it does |
|---|---|
| `uv run friday` (or `uv run python -m friday`) | Chat in the terminal. Ctrl+C cancels a task; Ctrl+C at the prompt (or `exit`) quits |
| `uv run friday voice` | Voice mode: say "Hey Friday" or press **Ctrl+Alt+F**, then speak |
| `uv run friday voice --no-wake` | Voice mode with the hotkey only (no always-on listening) |

While Friday is operating an app, keep your hands off the keyboard and mouse: one keyboard is shared, so your typing could land in her window.

In voice mode, press the hotkey again to finish recording early, or while Friday is talking to cut her off. The first run downloads a small (~40 MB) offline model used only to detect the wake phrase; your idle room audio is never sent to the cloud.

## Configuration

Set in `.env` (only the API key is required):

| Variable | Default | Meaning |
|---|---|---|
| `GOOGLE_GEMINI_API_KEY` | none | Your Gemini API key |
| `AGENT_MODELS` | 3.8-flash, 3.7-flash, 3.5-flash, 3.5-flash-lite | Models to use, tried in order if one is unavailable (JSON list) |
| `STT_MODEL` / `TTS_MODEL` | `gemini-3.5-flash-lite` / `gemini-3.8-flash-tts` | Speech models |
| `TTS_VOICE` | `Kore` | Gemini voice name (for example `Leda`, `Zephyr`, `Aoede`) |
| `FRIDAY_HOTKEY` | `ctrl+alt+f` | Global voice hotkey |
| `MIC_DEVICE` | Windows default | Microphone index or part of its name |
| `MAX_LLM_CALLS`, `MAX_TURNS_PER_STEP`, `MAX_STEP_RETRIES`, `MAX_REPLANS`, `TASK_TIMEOUT_S` | 40, 15, 2, 2, 900 | Limits for one task |
| `GUI_ENABLED` / `BROWSER_ENABLED` | `true` | Turn app control or the browser off |
| `BROWSER_CHANNELS` | `["chrome", "msedge"]` | Installed browsers to try, in order (JSON list) |
| `BROWSER_HEADLESS` | `false` | Run Friday's browser without a window |

Model prices for the cost estimate are in `src/friday/monitoring/pricing.toml`; the safety rules are in `src/friday/safety/rules.toml`.

## Safety

| Action | Behaviour |
|---|---|
| Reads, new files, appends, ordinary commands | Run automatically |
| Deletes (sent to the Recycle Bin), stopping processes, registry/service/permission changes, uninstalls, shutdown | Always ask |
| Clicking Delete, Don't save, Buy, Pay, Checkout, Place order and similar, in apps or on websites | Always ask |
| Closing a window, Alt+F4, Ctrl+W, Shift+Delete | Always ask |
| Overwriting an existing file | Always ask |
| Credential files (`.env`, `.ssh`, keys), Windows and Program Files, disk formatting, download-and-execute | Refused |

The rules live in `src/friday/safety/rules.toml`. Every non-read action is written to `runs/audit.jsonl`. The policy is a pattern-based gate, not a sandbox, so read approval prompts carefully.

## How it works

```
message -> triage + plan (1 call) -> direct answer, live web search answer, or steps
steps:  Act (tools) -> Observe -> Verify (independent check) -> Recover (retry / replan) -> Finish
```

Each step runs in a small fresh context. Retries and re-plans are capped, and every run has call, turn and time budgets. The Gemini client adds timeouts, retries, and a fallback chain across models.

## Project layout

```
src/friday/
  __main__.py  the `friday` command: text chat, or `friday voice`
  app.py       wiring: model, tools, safety, agent
  config.py    settings from .env
  agent/       loop (the phases), planner, verifier, context (model calls, events, budget),
               models (Plan, Verdict, run state), llm (model interface), gemini (Gemini client),
               prompts + system_prompts/*.md
  tools/       registry, files, shell, system, web, desktop, browser
  desktop/     automation, windows, input, elements, apps, screen, vision
  browser/     session, actions, snapshot
  voice/       chat, session, microphone, speech_to_text, text_to_speech, wake_word, hotkey
  safety/      policy + rules.toml, audit
  monitoring/  events, tracing, metrics + pricing.toml
  cli/         chat, display
tests/         flat test files (fake LLM, real PowerShell, files, hotkey, headless browser)
docs/          Product.md (vision and phases), Progress.md (what is built)
```

## Development

```powershell
uv run pytest          # tests
uv run ruff check .    # lint
uv run pyright         # strict type check
```

Runs write traces to `runs/` (ignored by git). See [docs/Product.md](docs/Product.md) for the product vision and roadmap, and [docs/Progress.md](docs/Progress.md) for what is built so far.
