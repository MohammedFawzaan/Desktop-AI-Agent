# Product

## What Friday is

Friday is an autonomous AI agent for Windows. You tell it what you want, by typing or by voice, and it works on your real PC: finding files and folders, writing and running things, and checking that the job is actually done. It is built to be fast, reliable and safe, and it runs on Google's Gemini models with its own purpose-built agent loop.

**Who it is for:** people who want an assistant that does things on their computer instead of only talking about them.

## Principles

- **Autonomous.** It discovers your system (folders, hardware, apps) itself; nothing is hard-coded.
- **Verified.** A task is done only when the result is checked against real system state, never because the model said so.
- **Safe.** Ordinary actions run on their own; destructive ones ask; secrets and system folders are off limits; everything is logged.
- **Fast and cheap.** Questions are answered in one call; plans are as short as possible; every call's cost is shown.
- **Transparent.** Each task ends with tokens, tools used, timings, verdicts and cost.

## How it works

```
message -> triage + plan (1 call) -> direct answer, or steps
steps:  Act (tools) -> Observe -> Verify -> Recover (retry / replan) -> Finish
```

Each step runs in a small fresh context, with capped retries, re-plans and budgets. A safety gate checks every tool call. The Gemini client adds timeouts, retries and a model fallback chain.

## Roadmap

| Phase | What | Status |
|---|---|---|
| 1 | **Core agent harness:** Gemini client, Plan/Act/Observe/Verify/Recover loop, typed tool registry, traces, first CLI | Done |
| 2 | **Chat-style CLI and acting on the real PC:** file and PowerShell tools, autonomous discovery, safety gate, per-task metrics | Done |
| 3 | **Voice control:** "Hey Friday" wake word and hotkey, Gemini speech-to-text, streamed text-to-speech | Done |
| 4 | **GUI and browser control:** see and operate desktop apps (UI Automation, screenshot + vision fallback) and websites in Friday's own browser (Playwright) | Done |
| 5 | **Agent memory:** persistent long-term memory of preferences, facts and past work | Next |
| 6 | **Web search and internet access:** search and information gathering on top of the phase 4 browser | Planned |
| 7 | **Frontend GUI app** for chatting, voice, history, approvals and metrics | Planned |
| 8 | **Agent harness improvements:** the agentic loop, tools, memory, reliability and speed | Planned |
| 9 | **Deployment:** installer, packaging, start with Windows | Last |

Focus for now is the core harness: the loop, its tools and its memory. Larger add-ons (evaluation suites, plugin systems, scheduling, multi-agent work) are deliberately out of scope until the core is strong.

## Tech stack

| Area | Today | Planned |
|---|---|---|
| Language | Python 3.13, uv | |
| LLM | Gemini (`gemini-3.8-flash`, fallbacks 3.7, 3.5, 3.5-lite) via `google-genai` | |
| Agent core | Custom async loop, Pydantic schemas and validation | |
| PC control | PowerShell, file tools, Windows APIs via ctypes, Windows UI Automation (comtypes), screenshots (mss, Pillow) with Gemini vision | |
| Voice | sounddevice, numpy, Vosk (local wake word), Gemini speech-to-text and text-to-speech, Windows hotkey API | Whisper as an optional offline speech-to-text |
| Safety | Pattern-based policy gate, Recycle Bin deletes, JSONL audit log | |
| Interface | typer and rich terminal | Frontend GUI app (technology to be decided) |
| Memory | Last 6 tasks per session | Persistent store (to be decided) |
| Web | Playwright browser control (installed Chrome or Edge) | Web search (provider to be decided) |
| Quality | pytest, ruff, pyright (strict), 164 tests | |
| Delivery | Run from source with uv | Packaged installer (to be decided) |

## Success measures

- Tasks finish correctly and are verified, with few retries.
- Plain questions answered in about 2 seconds; voice replies start within about 1.5 seconds of the answer.
- Low and visible cost per task (a typical file task is about 6 calls).
- No destructive action without the user's approval.
