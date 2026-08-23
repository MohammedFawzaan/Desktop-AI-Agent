# AI Desktop Agent — Complete Project Blueprint

---

## What Is This Product?

A locally-running AI-powered desktop agent that can **actually control your PC** through voice or text commands. It understands natural language, decides what actions to take, executes them on your operating system, and speaks the result back to you. It is not a chatbot. It is an agent — it takes real actions on your machine.

---

## Tech Stack (Both Versions)

| Layer | Technology | Why |
|---|---|---|
| Desktop App | Electron.js | Main process = Node.js backend. Renderer = React UI. One framework, no separate server. |
| UI | React | Runs inside Electron's renderer process |
| Backend Logic | Electron Main Process (Node.js) | LLM calls, agentic loop, all tools live here — no Express, no NestJS, no HTTP layer |
| LLM Brain | Claude API (Anthropic) or Gemini | Tool calling, decision making, responses |
| Voice STT | Web Speech API (Electron renderer) | Free, no package, built into Chromium |
| Voice TTS | Web Speech API (Electron renderer) | Free, no package, OS voices |
| IPC Bridge | Electron IPC (ipcMain / ipcRenderer) | Only bridge needed — renderer sends message, main process runs the agent |
| OS/Shell | Node.js `child_process` (built-in) | Execute system commands |
| File System | Node.js `fs` + `glob` package | File operations and search |
| Browser Control | Playwright | Automate real Chromium browser |
| Mouse/Keyboard | nut-js | OS-level input simulation |
| Clipboard | clipboardy | Read/write OS clipboard |

---

## Why Electron-Only (No Separate Backend)

Electron's main process **is** Node.js. All backend logic — LLM calls, agentic loop, tool execution — runs directly inside it. The renderer is just the React UI. They talk via IPC, which is faster than HTTP and needs zero server setup.

No Express. No NestJS. No ports. No server lifecycle to manage. Just Node.js modules calling each other directly as functions. This is the standard architecture for desktop AI agents.

---

## Project Folder Structure

```
ai-desktop-agent/
├── main.js                      # Electron main process — entry point, IPC handlers
├── preload.js                   # contextBridge — safely exposes IPC to renderer
├── src/                         # All backend logic (runs in main process)
│   ├── llm/
│   │   └── llmClient.js         # Claude/Gemini API client
│   ├── agent/
│   │   └── agentLoop.js         # The agentic loop engine
│   ├── tools/
│   │   ├── registry.js          # All tool definitions + executors combined
│   │   ├── osTools.js           # Feature 1: OS control
│   │   ├── fileTools.js         # Feature 2: File system
│   │   ├── browserTools.js      # Feature 3: Browser control
│   │   ├── terminalTools.js     # Feature 4: Terminal commands
│   │   └── inputTools.js        # Feature 5: Mouse & keyboard
│   └── memory/                  # V2 only
│       ├── embedder.js
│       ├── vectorStore.js
│       ├── memoryWriter.js
│       └── memoryRetriever.js
├── renderer/                    # React UI (runs in renderer process)
│   ├── index.html
│   ├── app.jsx
│   └── voice.js                 # Web Speech API — STT + TTS
├── package.json
└── .env                         # API keys
```

---

---

# VERSION 1 — MVP

---

## What Version 1 Includes

- Electron.js as the complete framework — main process handles all backend logic, renderer handles UI
- LLM brain with agentic loop running in Electron main process
- 5 core feature modules: OS Control, File System, Browser Control, Terminal Commands, Mouse & Keyboard Control
- Safety guardrail on terminal commands
- React UI in Electron renderer process
- Voice input and output via Web Speech API

---

## The 5 Core Features

---

### Feature 1: OS Control

**What it does:** Opens and closes applications, controls system settings like volume and brightness, locks screen, restarts or shuts down.

**How it works:** Node.js `child_process.exec()` fires platform-specific shell commands. The LLM decides which command to run based on the user's request. Your code maps the LLM's tool call to the correct OS command.

**Platform command examples:**
- Open app → `start spotify` (Windows) / `open -a Spotify` (macOS) / `spotify &` (Linux)
- Volume up → `nircmd.exe changesysvolume 5000` (Windows) / `osascript -e "set volume output volume 80"` (macOS)
- Lock screen → `rundll32.exe user32.dll,LockWorkStation` (Windows) / `pmset displaysleepnow` (macOS)

**Node.js packages needed:**
- `child_process` — built into Node.js, no install needed

**OS-level requirements:**
- Windows: Some commands need admin privileges
- macOS: Some system commands need Full Disk Access in System Preferences
- Linux: Standard user permissions sufficient for most commands

---

### Feature 2: File System Control

**What it does:** Searches for files by name or pattern across directories, reads file contents, creates, moves, renames, and deletes files.

**How it works:** Node's built-in `fs` module handles all read/write/delete/rename operations. The `glob` package handles recursive directory searches with pattern matching (e.g., find all PDFs, find files named "resume"). The LLM calls the appropriate tool with arguments like `{ pattern: "**/*.pdf", directory: "~/Documents" }` and your code executes it.

**Node.js packages needed:**
- `fs` — built into Node.js, no install needed
- `glob` — install via npm, handles pattern-based file searching across nested folders

**OS-level requirements:**
- macOS: Full Disk Access permission required to search protected directories
- Windows/Linux: Works with standard user permissions for accessible directories

---

### Feature 3: Web & Browser Control

**What it does:** Opens URLs, navigates web pages, clicks buttons, fills forms, scrapes page content, automates web workflows (e.g., search Google, fill a contact form, read an article).

**How it works:** Playwright launches and controls a real Chromium browser instance. Your code tells Playwright what to do (go to URL, find element, click, type) and Playwright executes it in a real visible or headless browser. The LLM decides the sequence of browser actions and calls tools one by one through the agentic loop.

**Node.js packages needed:**
- `playwright` — install via npm; on first install it downloads Chromium browser binaries (~150MB, one-time)

**OS-level requirements:**
- No special OS permissions needed
- Chromium binaries must be installed (Playwright handles this automatically)
- Headless or visible mode — your choice per use case

---

### Feature 4: Terminal Command Execution

**What it does:** Runs any shell/terminal command and returns the output back to the LLM so it can read results and decide next steps.

**How it works:** `child_process.exec()` runs the command in a shell, captures `stdout` and `stderr`, and returns them as the tool result. The LLM reads the output and either completes the task or chains another tool call.

**Safety layer (mandatory):** Terminal execution is the most dangerous tool. A mistaken LLM decision could delete files or break system configs. Implement one of these:
- **Whitelist approach** — Only allow pre-approved commands (safest)
- **Confirmation step** — Any destructive command (rm, del, format) requires user confirmation before execution
- **Dry-run flag** — LLM must set `confirm_required: true` for dangerous operations

**Node.js packages needed:**
- `child_process` — built into Node.js, no install needed

**OS-level requirements:**
- Windows: Some commands need elevated privileges (run app as admin)
- macOS/Linux: Standard shell access; sudo commands will prompt for password

---

### Feature 5: Mouse & Keyboard Control

**What it does:** Moves the mouse cursor, clicks anywhere on screen, double-clicks, right-clicks, scrolls, types text into any window, simulates keyboard shortcuts (Ctrl+C, Alt+Tab, etc.).

**How it works:** `nut-js` is a native Node.js addon that injects input events directly into the OS input system. This means it works in ANY window — not just your app. The LLM can use this as a fallback when no dedicated API exists for an app (e.g., control a legacy desktop app by clicking its buttons).

**Node.js packages needed:**
- `nut-js` — install via npm; it compiles native bindings on install (requires build tools)

**OS-level requirements:**
- macOS: **Accessibility permission required** — go to System Preferences → Security & Privacy → Accessibility → add your app. Without this, macOS blocks all input injection and nut-js will silently fail.
- Windows: Works out of the box, no special permissions
- Linux: Requires `libxtst-dev` system package installed (`sudo apt install libxtst-dev`)

**Build tools required (for nut-js native compilation):**
- Windows: `windows-build-tools` npm package or Visual Studio Build Tools
- macOS: Xcode Command Line Tools (`xcode-select --install`)
- Linux: `build-essential` package (`sudo apt install build-essential`)

---

## Agentic Loop

The agentic loop is the engine that makes the agent actually complete tasks rather than just suggesting what to do.

**The loop:**

```
1. User message arrives (text or voice transcript)
2. Append message to conversation history array
3. Send full history + all tool definitions to LLM
4. LLM responds with either:
   a. stop_reason = "end_turn" → extract text → send response → DONE
   b. stop_reason = "tool_use" → extract tool name + args → execute tool → get result
5. If tool was called:
   → Append tool result to conversation history
   → Go back to step 3 (loop again)
6. Repeat until end_turn
```

**Short-term memory:** The conversation history array IS the memory. Every message, tool call, and tool result is appended. The LLM sees the full session history on every turn. This resets when the app is closed.

**Max loop guard:** Add a max iterations limit (e.g., 10 tool calls per task) to prevent infinite loops if the LLM gets stuck.

---

## Voice Layer

### Speech-to-Text (STT)
**Technology:** Web Speech API — `window.SpeechRecognition` in Electron's renderer process

**How it works:** Electron runs Chromium under the hood, so the Web Speech API is natively available in the renderer. You call `SpeechRecognition.start()`, the browser captures mic input and returns a transcript string. You then send that string to your backend via Electron IPC.

**Limitation:** On some platforms, Chrome sends audio to Google's servers for processing. For a fully offline/private implementation, replace with `@xenova/transformers` running Whisper locally.

**No npm package needed** — Web API, available natively in Electron renderer.

### Text-to-Speech (TTS)
**Technology:** Web Speech API — `window.speechSynthesis` in Electron's renderer process

**How it works:** You call `speechSynthesis.speak(new SpeechSynthesisUtterance(text))` with the LLM's response text. The OS voices are used. Quality is decent for MVP. Swap to ElevenLabs API later for premium voice quality.

**No npm package needed** — Web API, available natively in Electron renderer.

---

## Electron IPC — How Renderer Talks to Main

Electron has two processes: **renderer** (React UI, has Web Speech API) and **main** (Node.js, runs all backend logic). They communicate via IPC — no HTTP, no ports, just direct inter-process messaging.

```
Renderer Process                    Main Process
(React UI, Web Speech API)          (Node.js — LLM, Tools, Agentic Loop)

User speaks
    ↓
SpeechRecognition captures text
    ↓
ipcRenderer.send('user-message', text)
    ────────────────────────────────→
                                    ipcMain.on('user-message')
                                        ↓
                                    agentLoop.run(text)
                                        ↓
                                    Tools execute on OS
                                        ↓
                                    LLM returns final response
                                        ↓
                                    event.reply('agent-response', text)
    ←────────────────────────────────
ipcRenderer.on('agent-response')
    ↓
speechSynthesis.speak(text)
    ↓
User hears response
```

The **preload script** safely exposes IPC methods to the renderer using `contextBridge` — never expose the full `ipcRenderer` object directly.

---

## V1 Full Package List

```json
{
  "dependencies": {
    "@anthropic-ai/sdk": "latest",
    "glob": "latest",
    "playwright": "latest",
    "nut-js": "latest",
    "clipboardy": "latest",
    "node-notifier": "latest",
    "dotenv": "latest"
  },
  "devDependencies": {
    "electron": "latest",
    "react": "latest",
    "react-dom": "latest"
  }
}
```

`child_process` and `fs` are built into Node.js — no install needed.

---

## V1 Build Phases

---

### Phase 1 — Backend

This is the first thing to build. No UI, no voice. Just the core brain and tools working via API calls or a test script. Verify everything works here before moving to Phase 2.

---

#### Step 1 — Project Setup & LLM Connection

- Init Electron project with React renderer
- Install `@anthropic-ai/sdk` and `dotenv`
- Set up `main.js` as Electron entry point
- Set up `preload.js` with `contextBridge`
- Create `src/llm/llmClient.js` — connect to Claude API, send a hardcoded message from main process, receive a response
- Verify the API key works and a response comes back
- At this point: Electron opens, main process can talk to the LLM. Nothing else yet.

---

#### Step 2 — Agentic Loop (Core Engine)

Build this before any real tools. The loop is the most important piece — everything else plugs into it.

- Create `agentLoop.js`
- Build the loop with a single **dummy tool** (a tool that just returns `"dummy tool called successfully"`)
- Verify: loop runs → LLM detects tool_use → dummy tool executes → result feeds back → LLM reaches end_turn
- At this point: the engine works. Ready to attach real tools.

---

#### Step 3 — Feature 1: OS Control Tools

- Create `osTools.js`
- Implement `open_application` tool using `child_process.exec()`
- Handle platform differences (Windows / macOS / Linux commands)
- Add to `registry.js` (tool definition + executor)
- Wire registry into `agentLoop.js`
- Add remaining OS tools: volume, brightness, lock screen
- **Test:** Send "Open VS Code" → verify app opens

---

#### Step 4 — Feature 2: File System Tools

- Create `fileTools.js`
- Install `glob`
- Implement `search_files` tool using glob pattern matching
- Implement `read_file` tool using `fs.readFile()`
- Add move/rename/delete tools with a confirmation guard on destructive operations
- Add to `registry.js`
- **Test:** Send "Find all PDF files in my Downloads folder" → verify results return

---

#### Step 5 — Feature 3: Terminal Command Tools

- Create `terminalTools.js`
- Implement `run_terminal_command` using `child_process.exec()`
- Capture both `stdout` and `stderr`, return both to LLM
- Implement the safety layer — whitelist OR confirmation flag for destructive commands
- Add to `registry.js`
- **Test:** Send "List files on my Desktop" → verify shell output returns to LLM correctly

---

#### Step 6 — Feature 4: Browser Control Tools

- Create `browserTools.js`
- Install `playwright` and run `npx playwright install chromium`
- Implement `open_url` tool — opens a URL in Chromium
- Implement `search_web` tool — navigates to Google, types query, returns results
- Implement `click_element` and `type_into_field` for form automation
- Add to `registry.js`
- **Test:** Send "Search YouTube for lo-fi music" → verify browser opens and navigates

---

#### Step 7 — Feature 5: Mouse & Keyboard Tools

- Create `inputTools.js`
- Install `nut-js`, handle native build (install build tools for your OS first)
- Grant Accessibility permission on macOS before testing
- Implement `mouse_click` — moves and clicks at coordinates
- Implement `type_text` — types into the currently focused window
- Implement `keyboard_shortcut` — fires key combos like Ctrl+C, Alt+Tab
- Add to `registry.js`
- **Test:** Send "Type hello in the focused window" → verify text appears

---

#### Step 8 — Backend Complete

- All 5 tools are in `registry.js`
- All tools are wired into `agentLoop.js`
- Test a multi-step chained task end-to-end: *"Find my resume and open it"*
  - LLM calls `search_files` → gets path → calls `open_application` with that path → done
- Add max loop iteration guard
- Add error handling in all tool executors
- **Backend is done.**

---

### Phase 2 — Frontend (Electron Renderer)

Build this after the backend logic in main process is fully working and tested.

- Build the React UI in `renderer/` — chat interface, input box, response display, thinking/loading state
- Set up IPC in `main.js`: `ipcMain.on('user-message')` → calls `agentLoop.js` → replies with result
- Renderer sends user message via `ipcRenderer`, receives response, displays it
- No HTTP, no server — just IPC function calls between renderer and main
- **Test:** Type a command in the UI → tool executes on OS → response appears in UI

---

### Phase 3 — Voice Layer

Build this after the Electron UI is working.

- Implement `SpeechRecognition` in `renderer/voice.js` using the Web Speech API
- On transcript received → send via `ipcRenderer` to main process
- Implement `speechSynthesis` in renderer — speak the agent's response text aloud
- Wire everything: voice in → IPC → agentLoop → IPC → voice out
- **Test:** Speak a command → agent executes it → speaks the result back
- Decide here whether to upgrade to local Whisper (`@xenova/transformers`) for offline/private STT

---

### Phase 4 — V1 Polish & Completion

- Full end-to-end test of all 5 features via voice
- Test multi-step chained tasks through voice
- Fix edge cases in tool executors
- Final safety review on terminal tools
- **V1 MVP complete.**

---

---

# VERSION 2 — With Memory, Context Awareness, RAG & Vector DB

---

## What Version 2 Adds on Top of V1

- Long-term memory that persists across sessions
- User preference learning over time
- Semantic memory retrieval using RAG (Retrieval-Augmented Generation)
- Local vector database (LanceDB) for storing and searching memories
- Local embedding model for converting text to vectors (no API cost)
- Automatic memory writing after every session
- Richer system prompt injection with retrieved context

Everything from V1 remains unchanged. Only the memory layer is added.

---

## The Memory Architecture — V2

### Two Types of Memory

**Short-term (same as V1):**
Conversation history array for the current session. Works exactly as V1. No changes needed.

**Long-term (new in V2):**
LanceDB vector database stored as files on disk inside the app's user data directory. Persists across sessions. Grows smarter over time.

---

## What Gets Stored in Vector DB

Every meaningful piece of information becomes a **chunk** — a piece of text that gets embedded and stored:

- Summary of each completed conversation session
- Every task the agent completed: what was asked, what tools were used, what paths/apps were involved
- User preferences discovered during conversations (preferred apps, working directories, habits)
- Corrections the user made to the agent (teaches it to not repeat mistakes)
- Frequently used file paths and application names
- Time-stamped activity patterns (morning routine, frequent tasks)

---

## The RAG Flow — V2

```
User says: "Open the project I was working on last week"
    ↓
1. EMBED the user's message
   → Local embedding model converts text to vector [0.23, 0.87, ...]
    ↓
2. SEARCH vector DB for closest semantic matches
   → Returns: "portfolio-app project, ~/projects/portfolio, VS Code, worked on Tuesday May 5"
   → Returns: "User prefers VS Code for all frontend projects"
    ↓
3. INJECT retrieved chunks into system prompt
   → "Relevant memory: User's active project is portfolio-app at ~/projects/portfolio. Prefers VS Code."
    ↓
4. AGENTIC LOOP runs with enriched context
   → LLM now knows exactly what to open without asking
    ↓
5. Task completes
    ↓
6. WRITE BACK to vector DB
   → Summarize what happened → embed → store for future retrieval
```

---

## New Packages for V2

| Package | Purpose |
|---|---|
| `vectordb` (LanceDB) | Local vector database, file-based, no server needed |
| `@xenova/transformers` | Runs embedding model locally in Node.js — converts text to vectors |

**Why LanceDB:** Runs entirely on-device. No server process. Stores as files in the app's data folder. Perfect for a desktop app. Node.js native.

**Why @xenova/transformers:** Runs embedding models (like `all-MiniLM-L6-v2`) locally in Node.js via ONNX runtime. Zero API cost. Works offline. First run downloads the model (~25MB), cached after.

---

## New Folder Structure — V2 Additions

```
ai-desktop-agent/
├── src/
│   ├── memory/                        ← NEW in V2
│   │   ├── embedder.js                # Loads local embedding model, embeds text
│   │   ├── vectorStore.js             # LanceDB init, insert, search operations
│   │   ├── memoryWriter.js            # Summarizes session and writes to DB after each conversation
│   │   └── memoryRetriever.js         # Searches DB given a query, returns relevant chunks
│   └── agent/
│       └── agentLoop.js               # MODIFIED: now calls memoryRetriever before LLM call
```

---

## Modified Agentic Loop — V2

```
1. User message arrives
2. RETRIEVE relevant memories from vector DB  ← new
3. Build system prompt with retrieved memories injected  ← new
4. Append user message to conversation history
5. Send history + enriched system prompt + tools to LLM
6. Agentic loop runs (identical to V1)
7. Final response sent to user
8. WRITE session summary to vector DB  ← new
9. Clear short-term history for next session
```

Only steps 2, 3, and 8 are new. Everything else is identical to V1.

---

## Memory Retrieval — How to Handle Recency vs Relevance

Vector search finds semantically similar memories — but not necessarily recent ones. Solve this by storing a timestamp as metadata alongside every chunk. When searching, apply a **hybrid filter**:

- Semantic similarity score (how related is it to the query?)
- Recency boost (prefer memories from the last 7 days when scores are close)
- For explicit time queries like "what did I do yesterday" — filter by timestamp directly, skip semantic search

---

## V2 Build Phases

---

### Phase 1 — Embedding Setup

- Install `@xenova/transformers`
- Create `embedder.js` — load `all-MiniLM-L6-v2` model
- Test: pass a sentence → receive a vector array back
- Model downloads ~25MB on first run, cached permanently after

---

### Phase 2 — Vector Store Setup

- Install `vectordb` (LanceDB)
- Create `vectorStore.js` — initialize DB in the app's user data directory
- Create a `memories` table with fields: `id`, `text`, `vector`, `timestamp`, `type` (preference / task / correction)
- Test: insert a dummy chunk → retrieve it back → confirm it works

---

### Phase 3 — Memory Writer

- Create `memoryWriter.js`
- After every session ends, call the LLM to summarize the conversation (2–3 sentences, focus on what tasks were done)
- Embed the summary using `embedder.js`
- Store in LanceDB with current timestamp and type = `session`
- Test: complete a session → check DB → verify summary was stored

---

### Phase 4 — Memory Retriever

- Create `memoryRetriever.js`
- On each new user message: embed the message → search LanceDB for top 5 most similar chunks
- Apply recency filter (boost recent results when similarity scores are close)
- For time-based queries ("what did I do yesterday") — filter by timestamp directly
- Return retrieved chunks as a formatted text block

---

### Phase 5 — Inject Into Agentic Loop

- Modify `agentLoop.js` to call `memoryRetriever` before each LLM call
- Append retrieved context to the system prompt under a `Relevant Memory` section
- Test: complete Session 1 with some tasks → close app → open app → ask about something from Session 1 → verify agent recalls it correctly

---

### Phase 6 — Preference Learning

- When user corrects the agent or states a preference ("I always use Chrome, not Firefox") — detect it
- Extract the preference statement and store it as type = `preference` in LanceDB
- In retrieval, give `preference` type chunks higher priority
- Test: tell the agent a preference → close app → new session → verify preference is applied automatically

---

### Phase 7 — V2 Complete

- Run full test: multiple sessions with varied tasks
- Verify memory retrieval is accurate and improves agent responses
- Verify preferences persist and are applied
- V2 is done

---

## V2 Full Package List (V1 packages + additions)

```json
{
  "dependencies": {
    "@anthropic-ai/sdk": "latest",
    "glob": "latest",
    "playwright": "latest",
    "nut-js": "latest",
    "clipboardy": "latest",
    "node-notifier": "latest",
    "dotenv": "latest",
    "vectordb": "latest",
    "@xenova/transformers": "latest"
  },
  "devDependencies": {
    "electron": "latest",
    "react": "latest",
    "react-dom": "latest"
  }
}
```

---

## Complete Version Upgrade Path

```
V1 — Phase 1: Backend
  → LLM connected
  → All 5 tools working
  → Agentic loop complete
  → Tested via script / API calls

V1 — Phase 2: Frontend
  → Electron UI
  → IPC bridge working
  → Chat interface functional

V1 — Phase 3: Voice
  → Web Speech API STT + TTS
  → Full voice pipeline end to end

V1 — Phase 4: Polish
  → Edge cases fixed
  → Safety guards in place
  → MVP complete ✓

        ↓

V2 — Phase 1–2: Storage Layer
  → Embedding model running locally
  → LanceDB initialized and tested

V2 — Phase 3–4: Memory Pipeline
  → Writer stores session summaries
  → Retriever fetches relevant context

V2 — Phase 5–6: Integration
  → Memory injected into agentic loop
  → Preference learning active

V2 — Phase 7: Complete ✓
  → Agent learns and remembers across all sessions
```

---

## OS Permission Checklist (Both Versions)

| Permission | OS | Required For | How to Grant |
|---|---|---|---|
| Accessibility | macOS | nut-js mouse/keyboard control | System Preferences → Security & Privacy → Accessibility |
| Full Disk Access | macOS | File system search in protected dirs | System Preferences → Security & Privacy → Full Disk Access |
| Microphone | macOS + Windows | Web Speech API voice input | Browser/OS prompt on first use |
| Admin/Elevated | Windows | Some terminal and OS commands | Run app as Administrator |
| libxtst-dev | Linux | nut-js input simulation | `sudo apt install libxtst-dev` |
| build-essential | Linux | nut-js native compilation | `sudo apt install build-essential` |

---

## Key Decisions & Recommendations

**Electron-only, no separate backend.** Electron's main process is Node.js — all backend logic (LLM, agentic loop, tools) lives there. No Express, no NestJS, no HTTP layer. Direct function calls between modules. This is standard for desktop AI agents.

**Claude vs Gemini:** Start with Claude. Tool calling is more reliable and well-documented. Gemini as a fallback option later.

**Database:** No database needed in V1. LanceDB in V2 for vector memory — file-based, no server.

**Voice quality:** Web Speech API is sufficient for MVP. Upgrade to ElevenLabs for TTS and Whisper (via `@xenova/transformers`) for STT when you want production quality.

**LangChain:** Skip for V1. Understand the raw implementation first. Consider adopting it in V2 specifically for its memory management utilities if the custom memory layer gets complex.

---

*Blueprint prepared for: AI Desktop Agent Project*
*Versions: V1 (MVP) and V2 (With RAG + Vector Memory)*
