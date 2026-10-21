# Progress

What is built and working so far in Friday, the autonomous Windows AI agent. Last updated 2026-10-20.

## Status

Phases 1-4 and 6 are complete (phase 5, memory, is next). Friday works from a typed chat or by voice ("Hey Friday" / hotkey), acts on the real PC through files, PowerShell, desktop app interfaces and her own browser, searches the web live, checks her own work, and shows detailed metrics after every task. 136 automated tests pass (including a real headless browser session and request racing); ruff and strict pyright are clean.

## What was built

### Phase 1 - Core agent harness
- Gemini client: per-request timeouts, retries on overload, a model fallback list, raw model turns kept intact (thought signatures).
- Agent loop: Plan, Act, Observe, Verify, Recover, Finish as an explicit state machine, with call, turn and time budgets.
- Tool registry: typed Python functions with Pydantic argument validation, risk levels, timeouts, and errors returned to the model as readable results.
- Events and JSONL traces for every run; the first CLI.

### Phase 2 - Chat-style CLI and acting on the real PC
- **Chat CLI** (`friday`): keeps the last 6 tasks as context so follow-ups work ("now delete it").
- **Instant answers:** greetings, facts, explanations and advice are answered in one call (about 2 s) with no plan label and no tools. Lookup-only questions are answered straight from the lookup without a verifier call.
- **Autonomous discovery:** nothing about the PC is hard-coded or put in prompts. The agent calls `get_system_context` (OS, hardware, RAM, drives, user, real Desktop/Documents/Downloads locations, time) or `find_paths` / `list_dir` when needed.
- **PC tools:** `get_system_context`, `read_file`, `list_dir`, `find_paths`, `write_file` (create, overwrite, append), `delete_path` (Recycle Bin), `run_command` (non-interactive PowerShell with timeout and process-tree kill).
- **Safety gate:** ordinary actions run automatically; deletes, stopping processes, registry/service/permission changes, uninstalls and shutdown always ask; overwriting a file asks; credential files, Windows and Program Files, disk formatting, download-and-execute and execution-policy changes are refused. A declined or blocked action tells the agent not to work around it. Every non-read action goes to `runs/audit.jsonl`.
- **Metrics after every task:** time, model calls by phase, input/output/thinking tokens and estimated cost; the full run is traced to `runs/`.

### Phase 3 - Voice control
- **`friday voice`:** say "Hey Friday" or press the global hotkey (default Ctrl+Alt+F), hear a beep, speak; Friday transcribes, acts, and answers out loud. The text chat is unchanged.
- **Local wake word:** a small offline Vosk model (~40 MB, downloaded once to `~/.friday/models`) listens for "Hey Friday"; idle audio never leaves the PC.
- **Speech models:** Gemini audio for speech-to-text (`gemini-3.5-flash-lite`, fallback `gemini-3.8-flash`) and text-to-speech (`gemini-3.8-flash-tts`, fallback `gemini-3.8-flash-lite-tts`; voice set with `TTS_VOICE`, currently Leda).
- **Streamed speech:** audio plays as it is generated, so sound starts about 1.3 s after the answer is ready (whole-file method: about 5 s).
- **Short spoken replies:** in voice mode the agent answers in 1-3 plain sentences; markdown, code and links are stripped before speaking, while the full answer is still printed.
- **Hotkey controls:** pressing it again ends recording early, or interrupts Friday mid-sentence.
- **Microphone handling:** uses the Windows default mic through WASAPI (the older default path returned silence); `MIC_DEVICE` overrides it.

### Phase 4 - GUI and browser control
- **Desktop apps (Windows UI Automation):** `list_windows`, `read_window` (a window's buttons, fields, menus and text as a numbered list), `click`, `type_text`, `press_keys`, `scroll`, `focus_window`, `window_action` (minimize, maximize, restore, close), `open_app` (any Start-menu app by name, typos tolerated, waits for its window).
- **Fast, exact reading:** one batched UI Automation call reads a whole window (about 0.02-0.08 s). Chromium and Electron apps, which hide their controls until asked, are woken up automatically.
- **Every action reports what changed:** the focused window and any new windows (dialogs, popups), so the verifier can confirm the effect.
- **Vision fallback:** `inspect_screen` sends a screenshot to Gemini when an app does not expose its controls; it answers the question and returns click positions.
- **Browser control (Playwright):** Friday has her own browser window using the installed Chrome (Edge as fallback) with a separate profile, so the user's normal browser is untouched and sign-ins in Friday's browser are remembered. Tools: `browser_open`, `browser_read`, `browser_click`, `browser_type`, `browser_press`, `browser_select`, `browser_scroll`, `browser_back`, `browser_tabs`. Pages come back as numbered links, buttons and inputs plus their visible text; password values are masked. `open_url` still opens a page in the user's own browser.
- **Safety for GUI and web:** clicks on things like Delete, Don't save, Uninstall, Buy, Pay, Checkout or Place order always ask; closing a window or pressing Alt+F4 / Ctrl+W / Shift+Delete asks.
- **Metrics:** vision calls made inside tools are counted in the token and cost report.
- **Speed work after adding 20 tools:** each plan step names the tool groups it needs ('pc', 'apps', 'web') and only those are sent to the model (all of them on a retry); older tool outputs inside a step are trimmed; fixed waits after GUI and browser actions were shortened and page snapshots made smaller.

### Phase 6 - Web search and internet access
- **Questions answered from a live search:** the planner now has three choices: answer directly (stable knowledge), `search_query` (a question whose answer can change), or steps. A search question is answered with one Gemini call that has Google Search grounding turned on; the answer lists its sources (not read aloud in voice mode).
- **Safety net:** the planner also states whether its answer could be outdated (`can_be_outdated`); if so, Friday answers from a live search instead of memory. The model decides; there is no keyword list.
- **`web_search` tool:** a sourced answer from live Google results in one call, with source links, for multi-step tasks. Its model call shows up as "search" in the metrics.
- **`fetch_page` tool:** reads a page's text directly over HTTP (httpx plus a small HTML-to-text extractor), no browser needed; refuses non-web files and asks for the browser when a page needs JavaScript.
- **Browser only for interaction:** the actor uses `web_search` and `fetch_page` for information and keeps the Playwright browser for clicks, logins and forms (DuckDuckGo in the browser is only a fallback).
- **System context gives the country:** `get_system_context` now includes the Windows region (e.g. IN) next to the local time and time zone, so "the price of dollars" means INR without extra commands.
- Live check: "What's the current price of dollars?" went from 13 calls, 63k tokens, 37 s, about $0.05 (browser) to 5 calls, 12.5k tokens, about $0.01; "latest AI news today" is answered in 2 calls with sources.

### Grounding: no stale or invented answers
- **Planner rules:** only stable knowledge (concepts, maths, code, advice, small talk) is answered from memory. News, today/latest/recent, prices, exchange rates, weather, scores, versions, what a real person or company did recently, and this PC's state are always looked up. Conversation notes are treated as summaries, not sources.
- **Self-check:** the planner's `can_be_outdated` judgement sends possibly outdated answers to a live web search (this replaced an earlier keyword regex).
- **Research rules for the actor:** search with `web_search`, read sources with `fetch_page` (the browser with DuckDuckGo only as a fallback), answer only from what the tools showed, say "not found" instead of guessing, mention source and recency.
- **Verifier fact check:** every name, number, date and claim in an information answer must be supported by tool output, otherwise the step is retried; the final summary may contain only supported facts.

### Code structure refactor
- Package renamed from `desktop_ai_agent` to `friday`; `python -m friday` also works.
- Eight domain folders: agent, tools, desktop, browser, voice, safety, monitoring, cli. `cli/` holds only text chat and `voice/` only voice mode; `__main__.py` routes between them. Tests are flat files in `tests/`.

### Simplification (2026-10-20)
- **Organised:** types are apart from logic (`agent/models.py`); each phase has its own file (`planner.py`, `verifier.py`, `loop.py` for act, observe and recover); every model call goes through one `RunContext` that keeps the budget, usage and events; the Gemini client (`gemini.py`) is separate from the model interface (`llm.py`).
- **Less hard-coding:** system prompts are Markdown files (`agent/system_prompts/`), safety rules are in `safety/rules.toml`, model prices in `monitoring/pricing.toml`, and models, limits and timeouts in `config.py` / `.env`.
- **Extras removed:** hedged (duplicate) requests, quota-cooldown parsing, the whole-file speech fallback, speech-model fallback chains, verifier probes (the verifier is one structured call), stuck detection (the turn limit covers it), "always allow" approvals, the detailed metrics tables, the `friday run` and `friday mics` commands, the `-v` and `--trace` options, and the time-sensitive keyword regex.
- **Kept:** every tool, the Plan, Act, Observe, Verify, Recover loop, web search answers, safety, voice with wake word and hotkey, streamed speech, traces and the cost estimate.
- Source went from about 6,100 to 5,100 lines; 136 tests pass, ruff and strict pyright are clean, and chat was checked live (direct answer 1 call, web question 2 calls, PC task 5 calls).

## How the agent works

1. **Plan (one call):** answer directly (stable knowledge), answer from one live web search (anything that can be outdated), or produce the fewest steps, each with an observable expected outcome and the tool groups it needs.
2. **Act / Observe:** one step at a time in a fresh small context; reads run together, changes run in order and a failure skips the rest of that turn; older tool results are shortened.
3. **Verify:** an independent structured check of the tool outputs. The last step's check also judges the whole goal; steps that only read skip it.
4. **Recover:** retry the step (max 2), then make a new plan (max 2), then give up; `blocked` stops.
5. **Limits:** 40 model calls, 15 turns per step, 15 minutes (all configurable).

## Measured results

| Measure | Before | After |
|---|---|---|
| LLM calls, benchmark file task | 18 | 6 |
| Tokens, same task | 11.3k | 5.0k |
| Plain question | 2.2-3.8 s | about 2.1-2.5 s, 1 call |
| System-specs question | 4 calls | 3 calls, 4.9 s |
| Time to first speech | 4.9 s | 1.3 s |

Live phase 4 runs: "open Calculator and work out 25 times 4" took 23 s and 10 LLM calls (about $0.02); "find who designed Python on Wikipedia" took 21 s and 9 calls (about $0.03), both verified.
After the speed work the same two tasks took 7 calls each instead of 10 and 9, and the web task's prompts shrank from 4.5k to 3.2k tokens on average (8.5k to 5.1k at most). Wall time stayed around 23-25 s because Gemini's per-call latency varied between runs (2.0-2.9 s); most of a GUI task's time is now that latency multiplied by the number of steps.

## Tech stack (in use)

| Area | Tool |
|---|---|
| Language, packaging | Python 3.13, uv |
| LLM | Google Gemini via `google-genai` (`gemini-3.8-flash` primary, fallbacks 3.7, 3.5, 3.5-lite) |
| Data validation, config | Pydantic, pydantic-settings |
| Terminal UI | typer, rich |
| Reliability | tenacity |
| Safe deletes | send2trash (Recycle Bin) |
| Audio | sounddevice, numpy |
| Wake word | vosk with `vosk-model-small-en-us-0.15` |
| Hotkey | Windows RegisterHotKey via ctypes |
| Speech | Gemini audio models (STT and TTS) |
| Desktop app control | Windows UI Automation via comtypes, SendInput via ctypes |
| Screenshots, vision | mss, Pillow, Gemini vision |
| Browser control | Playwright driving the installed Chrome or Edge |
| Quality | pytest, pytest-asyncio, ruff, pyright (strict) |

## Layout

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

## How to use

- Chat: `uv run friday`.
- GUI and web: just ask, e.g. "open Calculator and work out 25 times 4" or "search Wikipedia for X".
- Voice: `uv run friday voice` (`--no-wake` for hotkey only; set `FRIDAY_HOTKEY` and `MIC_DEVICE` in `.env`).
- Traces: `runs/<timestamp>.jsonl`; safety audit: `runs/audit.jsonl`.
- Checks: `uv run pytest`, `uv run ruff check .`, `uv run pyright`.

## Known limits

- While Friday drives an app, your own typing or clicks can land in that app (one keyboard and mouse are shared). Let her finish GUI tasks hands-off.
- `read_window` sees only what apps expose to UI Automation; canvas-drawn apps and games need `inspect_screen`, which costs a vision call.
- Friday's browser uses its own profile: sign in to sites there once. Only one Friday session can use that browser at a time.
- Browser snapshots cover the main page, not content inside iframes.
- The wake word was tuned on synthetic speech; other voices or noisy rooms may need threshold tuning.
- The mic is paused while Friday speaks; the hotkey is the way to interrupt her.
- Approvals for risky actions are typed (y/n), not spoken.
- The safety policy is a pattern-based gate, not a sandbox; an unusually phrased destructive command could slip past it.
- Ctrl+C handling and live approval prompts were tested with piped input; check them in a real terminal.
- Fallback models may reject another model's thought signatures mid-step (not yet tested).
- Web search does not know today's date, so "latest" questions can return an older result (seen once with "who won the last IPL final").
- The Gemini key's project needs a paid tier (the free tier allows about 20 requests per day per model).
