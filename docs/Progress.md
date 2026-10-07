# Progress

What is built and working so far in Friday, the autonomous Windows AI agent. Last updated 2026-10-08.

## Status

Phases 1-3 are complete. Friday works from a typed chat or by voice ("Hey Friday" / hotkey), acts on the real PC (files and PowerShell), checks its own work, and shows detailed metrics after every task. 104 automated tests pass; ruff and strict pyright are clean.

## What was built

### Phase 1 - Core agent harness
- Gemini client: per-request timeouts, retries on overload, a model fallback chain with quota-aware cooldowns, raw model turns kept intact (thought signatures).
- Agent loop: Plan, Act, Observe, Verify, Recover, Finish as an explicit state machine, with call, turn and time budgets.
- Tool registry: typed Python functions with Pydantic argument validation, risk levels, timeouts, and errors returned to the model as readable results.
- Events and JSONL traces for every run; the first CLI.

### Phase 2 - Chat-style CLI and acting on the real PC
- **Chat CLI** (`friday`): keeps the last 6 tasks as context so follow-ups work ("now delete it").
- **Instant answers:** greetings, facts, explanations and advice are answered in one call (about 2 s) with no plan label and no tools. Lookup-only questions are answered straight from the lookup without a verifier call.
- **Autonomous discovery:** nothing about the PC is hard-coded or put in prompts. The agent calls `get_system_context` (OS, hardware, RAM, drives, user, real Desktop/Documents/Downloads locations, time) or `find_paths` / `list_dir` when needed.
- **PC tools:** `get_system_context`, `read_file`, `list_dir`, `find_paths`, `write_file` (create, overwrite, append), `delete_path` (Recycle Bin), `run_command` (non-interactive PowerShell with timeout and process-tree kill).
- **Safety gate:** ordinary actions run automatically; deletes, stopping processes, registry/service/permission changes, uninstalls and shutdown always ask; overwriting a file asks; credential files, Windows and Program Files, disk formatting, download-and-execute and execution-policy changes are refused. A declined or blocked action tells the agent not to work around it. Every non-read action goes to `runs/audit.jsonl`.
- **Metrics after every task:** status, time (LLM vs tools), steps, retries and replans, LLM calls, input/output/thinking tokens (by phase, and per call with `-v`), models used, tools table with outputs, verification verdicts, estimated cost.

### Phase 3 - Voice control
- **`friday voice`:** say "Hey Friday" or press the global hotkey (default Ctrl+Alt+F), hear a beep, speak; Friday transcribes, acts, and answers out loud. The text chat is unchanged.
- **Local wake word:** a small offline Vosk model (~40 MB, downloaded once to `~/.friday/models`) listens for "Hey Friday"; idle audio never leaves the PC.
- **Speech models:** Gemini audio for speech-to-text (`gemini-3.5-flash-lite`, fallback `gemini-3.8-flash`) and text-to-speech (`gemini-3.8-flash-tts`, fallback `gemini-3.8-flash-lite-tts`; voice set with `TTS_VOICE`, currently Leda).
- **Streamed speech:** audio plays as it is generated, so sound starts about 1.3 s after the answer is ready (whole-file method: about 5 s). The whole-file path is the automatic fallback.
- **Short spoken replies:** in voice mode the agent answers in 1-3 plain sentences; markdown, code and links are stripped before speaking, while the full answer is still printed.
- **Hotkey controls:** pressing it again ends recording early, or interrupts Friday mid-sentence.
- **Microphone handling:** uses the Windows default mic through WASAPI (the older default path returned silence); `friday mics` tests it; `MIC_DEVICE` overrides it; a warning appears if the mic is silent.
- **Voice metrics:** a table of the speech-to-text and text-to-speech calls after each turn.

## How the agent works

1. **Triage + plan (one call, low thinking):** answers directly if the message needs no PC access; otherwise produces the fewest steps, each with an observable expected outcome.
2. **Act / Observe:** one step at a time in a fresh small context; tools run in order, a failure skips the rest of that turn, pure reads run concurrently.
3. **Verify:** an independent verifier judges from tool outputs, can re-check fresh state with read-only tools, and submits its verdict in the same call. The last step's verdict also judges the whole goal; read-only steps skip verification.
4. **Recover:** retry the step (max 2), then replan (max 2), then give up; `blocked` stops. Repeating the same call with the same output forces a replan.
5. **Budgets:** 40 LLM calls, 10 turns per step, 15 minutes.

## Measured results

| Measure | Before | After |
|---|---|---|
| LLM calls, benchmark file task | 18 | 6 |
| Tokens, same task | 11.3k | 5.0k |
| Plain question | 2.2-3.8 s | about 2.1-2.5 s, 1 call |
| System-specs question | 4 calls | 3 calls, 4.9 s |
| Time to first speech | 4.9 s | 1.3 s |

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
| Quality | pytest, pytest-asyncio, ruff, pyright (strict) |

## Layout (`src/desktop_ai_agent/`)

`llm/` Gemini client · `agent/` loop, state, prompts · `tools/` registry and PC tools · `safety/` policy and audit log · `obs/` events, traces, metrics · `voice/` audio, mic, wake word, hotkey, player, session · `cli/` chat, voice mode, reports · `env.py` system discovery · `config.py` settings. Tests in `tests/`.

## How to use

- Chat: `uv run friday` (`-v` for full metrics). One task: `uv run friday run "<task>"`.
- Voice: `uv run friday voice` (`--no-wake`, `--hotkey ctrl+alt+g`); test the mic with `uv run friday mics`.
- Traces: `runs/<timestamp>.jsonl`; safety audit: `runs/audit.jsonl`.
- Checks: `uv run pytest`, `uv run ruff check .`, `uv run pyright`.

## Known limits

- The wake word was tuned on synthetic speech; other voices or noisy rooms may need threshold tuning.
- The mic is paused while Friday speaks; the hotkey is the way to interrupt her.
- Destructive-action approvals are typed (y/n), not spoken.
- The safety policy is a pattern-based gate, not a sandbox; an unusually phrased destructive command could slip past it.
- Ctrl+C handling and live approval prompts were tested with piped input; check them in a real terminal.
- Fallback models may reject another model's thought signatures mid-step (not yet tested).
- The Gemini key's project needs a paid tier (the free tier allows about 20 requests per day per model).
