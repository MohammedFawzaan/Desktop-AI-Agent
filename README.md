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
- **Uses websites.** Has its own browser window (Playwright on your installed Chrome or Edge, separate profile) to open pages, read them, click, fill forms and search.
- **Plans, acts, verifies, recovers.** Every task runs through Plan, Act, Observe, Verify, Recover. A task only counts as done when the result is checked against real system state; failures are retried or re-planned.
- **Answers questions instantly.** Greetings, facts and advice are answered directly in one call (about 2 seconds), with no planning or tools.
- **Voice control.** Say "Hey Friday" or press a global hotkey, then speak. Replies are spoken back, streamed so sound starts about 1.3 seconds after the answer is ready.
- **Safe by default.** Ordinary actions run automatically; destructive ones (deletes, killing processes, registry changes) ask first; secrets and system folders are off limits. Everything is logged.
- **Shows its work.** After every task: tokens (input, output, thinking), tools called with their outputs, timings, verification verdicts and an estimated cost.

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
| `uv run friday` | Chat in the terminal. Ctrl+C cancels a task; Ctrl+C at the prompt (or `exit`) quits |
| `uv run friday run "<task>"` | Run one task and exit |
| `uv run friday voice` | Voice mode: say "Hey Friday" or press **Ctrl+Alt+F**, then speak |
| `uv run friday voice --no-wake` | Voice mode with the hotkey only (no always-on listening) |
| `uv run friday mics` | List microphones and test your voice level |
| add `-v` to any command | Full metrics detail |

While Friday is operating an app, keep your hands off the keyboard and mouse: one keyboard is shared, so your typing could land in her window.

In voice mode, press the hotkey again to finish recording early, or while Friday is talking to cut her off. The first run downloads a small (~40 MB) offline model used only to detect the wake phrase; your idle room audio is never sent to the cloud.

## Configuration

Set in `.env` (only the API key is required):

| Variable | Default | Meaning |
|---|---|---|
| `GOOGLE_GEMINI_API_KEY` | none | Your Gemini API key |
| `AGENT_MODEL` | `gemini-3.8-flash` | Main model |
| `AGENT_FALLBACK_MODELS` | 3.7-flash, 3.5-flash, 3.5-flash-lite | Used in order if a model is overloaded (JSON list) |
| `FRIDAY_HOTKEY` | `ctrl+alt+f` | Global voice hotkey |
| `TTS_VOICE` | `Kore` | Gemini voice name (for example `Leda`, `Zephyr`, `Aoede`) |
| `STT_MODELS` / `TTS_MODELS` | flash-lite / flash-tts | Speech models (JSON lists) |
| `MIC_DEVICE` | Windows default | Microphone index or part of its name |
| `GUI_ENABLED` / `BROWSER_ENABLED` | `true` | Turn app control or the browser off |
| `BROWSER_CHANNELS` | `["chrome", "msedge"]` | Installed browsers to try, in order (JSON list) |
| `BROWSER_HEADLESS` | `false` | Run Friday's browser without a window |

## Safety

| Action | Behaviour |
|---|---|
| Reads, new files, appends, ordinary commands | Run automatically |
| Deletes (sent to the Recycle Bin), stopping processes, registry/service/permission changes, uninstalls, shutdown | Always ask |
| Clicking Delete, Don't save, Buy, Pay, Checkout, Place order and similar, in apps or on websites | Always ask |
| Closing a window, Alt+F4, Ctrl+W | Ask (can answer "always") |
| Overwriting an existing file | Ask (can answer "always") |
| Credential files (`.env`, `.ssh`, keys), Windows and Program Files, disk formatting, download-and-execute | Refused |

Every non-read action is written to `runs/audit.jsonl`. The policy is a pattern-based gate, not a sandbox, so read approval prompts carefully.

## How it works

```
message -> triage + plan (1 call) -> direct answer, or steps
steps:  Act (tools) -> Observe -> Verify (independent check) -> Recover (retry / replan) -> Finish
```

Each step runs in a small fresh context. Retries and re-plans are capped, and every run has call, turn and time budgets. The Gemini client adds timeouts, retries, and a fallback chain across models.

## Project layout

```
src/desktop_ai_agent/
  agent/   loop, state, prompts          llm/     Gemini client
  tools/   registry + PC tools           safety/  policy + audit log
  voice/   mic, wake word, hotkey, TTS   obs/     events, traces, metrics
  gui/     UI Automation, input, vision  browser/ Playwright browser
  cli/     chat, voice mode, reports     env.py   system discovery
tests/     164 tests (fake LLM, real PowerShell, files, hotkey, headless browser)
docs/      Product.md (vision and phases), Progress.md (what is built)
```

## Development

```powershell
uv run pytest          # tests
uv run ruff check .    # lint
uv run pyright         # strict type check
```

Runs write traces to `runs/` (ignored by git). See [docs/Product.md](docs/Product.md) for the product vision and roadmap, and [docs/Progress.md](docs/Progress.md) for what is built so far.
