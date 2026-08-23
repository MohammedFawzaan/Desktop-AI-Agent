# Friday — Tools Overview

This document describes every tool the agent can use to take real actions on the
computer. Each tool is a self-contained LangChain `tool()` object (schema +
function) registered in [`src/registry/tools.js`](../src/registry/tools.js) and
exposed to the LLM via the ReAct agent in
[`src/agent/agentLoop.js`](../src/agent/agentLoop.js).

## How tools fit together

```
User (text / voice)
   │
   ▼
ReAct Agent (LangChain createAgent + Gemini)
   │  decides which tool to call
   ▼
Tool registry (Zod-validated)  ──►  Tool function  ──►  OS / apps / filesystem
   │                                                        │
   └──────────────  result (JSON string)  ◄────────────────┘
```

- **Schema validation:** every tool defines a [Zod](https://zod.dev) schema, so
  the LLM's arguments are type-checked before the function runs.
- **Uniform return shape:** each underlying function returns
  `{ success: boolean, result: string }`, which the registry serializes to JSON
  for the model.
- **Platform aware:** OS-specific tools branch on
  `getSystemInfo().result.system.platform` (`win32` / `darwin` / linux).

---

## Tool reference

### 1. `open_application`
- **File:** [`src/tools/osTools.js`](../src/tools/osTools.js)
- **What it does:** Launches a desktop app by friendly name (e.g. "chrome",
  "notepad", "vs code").
- **Tech:** PowerShell (run via `-EncodedCommand`, base64 UTF-16LE, to avoid
  quoting issues) + the [`open`](https://github.com/sindresorhus/open) package.
- **How it works (resolve → launch):**
  1. **Resolve** the name to a path with PowerShell:
     `Get-Command` (PATH) → `App Paths` registry → `Get-StartApps` (Start menu / UWP).
  2. **Launch:** real executables are launched with `open`'s `openApp(path)`;
     UWP/Store apps are launched via `shell:AppsFolder\<AppID>`.
- **Note:** It always attempts to launch (no "already running" guard) — relaunching
  simply focuses the app or opens a new window.

### 2. `close_application`
- **File:** [`src/tools/osTools.js`](../src/tools/osTools.js)
- **What it does:** Closes a running app by name.
- **Tech:** [`ps-list`](https://github.com/sindresorhus/ps-list) + Node's
  `process.kill`.
- **How it works:** Lists processes, matches by exact name first then a substring
  fallback (so "chrome" catches every `chrome.exe`), and kills each matching PID.

### 3. `search_files`
- **File:** [`src/tools/fileTools.js`](../src/tools/fileTools.js)
- **What it does:** Finds files by name or glob pattern.
- **Tech:** [`fast-glob`](https://github.com/mrmlnc/fast-glob) (2–5× faster than
  `glob`).
- **How it works:** Plain filenames (`report.pdf`) are auto-expanded to a
  recursive `**/report.pdf`. If nothing is found in the target directory and none
  was specified, it broadens the search to desktop / documents / downloads /
  pictures. Heavy/irrelevant trees (`node_modules`, `AppData`, `Windows`, `.git`)
  are ignored, and permission errors are suppressed so a scan never crashes.

### 4. `write_file`
- **File:** [`src/tools/fileTools.js`](../src/tools/fileTools.js)
- **What it does:** Creates or overwrites a text file.
- **Tech:** Node `fs/promises`.
- **How it works:** Resolves the directory (named folders like `desktop` map to
  real paths), creates it recursively if missing, then writes the content as UTF-8.

### 5. `open_file`
- **File:** [`src/tools/fileTools.js`](../src/tools/fileTools.js)
- **What it does:** Opens a file or URL in its default application (PDF → viewer,
  image → photo app, webpage → browser).
- **Tech:** The [`open`](https://github.com/sindresorhus/open) package.
- **How it works:** `http(s)` URLs open directly; absolute paths open as-is; a bare
  filename is resolved against common folders and, if missing there, located via a
  recursive search before opening.

### 5a. `read_file`
- **File:** [`src/tools/fileTools.js`](../src/tools/fileTools.js)
- **What it does:** Reads and returns the text contents of a file.
- **Tech:** Node `fs/promises`.
- **How it works:** Resolves the path (named folders or recursive search), rejects
  directories, and returns the content capped at 100 KB so large files don't flood
  the model. Used to answer questions about a file or before editing it.

### 5b. `list_directory`
- **File:** [`src/tools/fileTools.js`](../src/tools/fileTools.js)
- **What it does:** Lists the files and subfolders inside a directory.
- **Tech:** Node `fs/promises` (`readdir` with file types).
- **How it works:** Folders are listed first (tagged `[dir]`), then files, capped at
  100 entries. Accepts a named folder or full path; defaults to home.

### 5c. `delete_file`
- **File:** [`src/tools/fileTools.js`](../src/tools/fileTools.js)
- **What it does:** Permanently deletes a file or folder.
- **Tech:** Node `fs/promises` (`rm` recursive).
- **How it works:** Resolves the target then removes it. **Permanent — no recycle bin.**

### 5d. `move_file`
- **File:** [`src/tools/fileTools.js`](../src/tools/fileTools.js)
- **What it does:** Moves or renames a file (rename = move within the same folder).
- **Tech:** Node `fs/promises` (`rename`, with `copyFile`+`rm` fallback across drives).
- **How it works:** Resolves the source, creates the destination folder if needed,
  then renames; falls back to copy-then-delete on cross-device (`EXDEV`) moves.

### 5e. `copy_file`
- **File:** [`src/tools/fileTools.js`](../src/tools/fileTools.js)
- **What it does:** Copies a file to a new location or name.
- **Tech:** Node `fs/promises` (`copyFile`).
- **How it works:** Resolves the source, creates the destination folder if needed,
  then copies.

### 6. `run_terminal_command`
- **File:** [`src/tools/terminalTools.js`](../src/tools/terminalTools.js)
- **What it does:** Runs an arbitrary shell command and returns its output.
- **Tech:** Node `child_process.exec` (15s timeout).
- **How it works:** Executes the command and returns stdout (or stderr / error
  message). This is the agent's general-purpose escape hatch when no dedicated tool
  fits.

### 7. `get_system_info`
- **File:** [`src/tools/systemTools.js`](../src/tools/systemTools.js)
- **What it does:** Reports OS, username, file-system paths, CPU, memory, network,
  and which common CLIs (node, git, python…) are installed.
- **Tech:** Node `os` module + `execSync` (`where`/`which`); result is cached.
- **How it works:** The agent calls this first for any path/OS task so it never has
  to guess paths — everything is discovered dynamically.

### 8. `get_running_processes`
- **File:** [`src/tools/systemTools.js`](../src/tools/systemTools.js)
- **What it does:** Lists running processes.
- **Tech:** [`ps-list`](https://github.com/sindresorhus/ps-list).
- **How it works:** Returns structured data grouped by app name with instance
  counts and sample PIDs — far easier for the model to parse than raw `tasklist`.

### 9. `take_screenshot`
- **File:** [`src/tools/automationTools.js`](../src/tools/automationTools.js)
- **What it does:** Captures the screen to a PNG (defaults to the desktop).
- **Tech:** [`@nut-tree-fork/nut-js`](https://github.com/nut-tree/nut.js) `screen.capture`.
- **How it works:** Lets the agent "see" the current screen before interacting with
  the UI.

### 10. `click_at`
- **File:** [`src/tools/automationTools.js`](../src/tools/automationTools.js)
- **What it does:** Moves the mouse to pixel coordinates `(x, y)` and clicks.
- **Tech:** nut.js `mouse` (`setPosition`, `click`, `doubleClick`).
- **How it works:** Supports left / right / middle buttons and double-click.
  Coordinates are pixels from the top-left of the primary screen.

### 11. `type_text`
- **File:** [`src/tools/automationTools.js`](../src/tools/automationTools.js)
- **What it does:** Types text into the currently focused application.
- **Tech:** nut.js `keyboard.type`.
- **How it works:** Simulates real keystrokes into whatever window has focus.

### 12. `press_key`
- **File:** [`src/tools/automationTools.js`](../src/tools/automationTools.js)
- **What it does:** Presses a key or key combination (chord), e.g. `Ctrl+S`,
  `Alt+F4`, `Win+D`.
- **Tech:** nut.js `keyboard.pressKey` / `releaseKey` with a friendly-name → `Key`
  enum map.
- **How it works:** Accepts an array of key names; modifiers and the final key are
  held down in order and released in reverse to form a proper chord.

### 13. `get_clipboard`
- **File:** [`src/tools/automationTools.js`](../src/tools/automationTools.js)
- **What it does:** Reads the current text contents of the system clipboard.
- **Tech:** nut.js `clipboard.getContent`.

### 14. `set_clipboard`
- **File:** [`src/tools/automationTools.js`](../src/tools/automationTools.js)
- **What it does:** Writes text to the system clipboard so the user can paste it.
- **Tech:** nut.js `clipboard.setContent`.

### 15. `get_active_window`
- **File:** [`src/tools/automationTools.js`](../src/tools/automationTools.js)
- **What it does:** Reports the title and owning app of the window the user is
  currently focused on.
- **Tech:** [`active-win`](https://github.com/sindresorhus/active-win).
- **How it works:** Lets the agent understand what the user is looking at before
  acting on it.

---

## Capability map

| Category | Tools |
|----------|-------|
| **Apps** | `open_application`, `close_application` |
| **Files — read** | `read_file`, `list_directory`, `search_files` |
| **Files — write** | `write_file`, `move_file`, `copy_file`, `delete_file`, `open_file` |
| **System** | `get_system_info`, `get_running_processes`, `run_terminal_command` |
| **UI automation** | `take_screenshot`, `click_at`, `type_text`, `press_key` |
| **Context / clipboard** | `get_active_window`, `get_clipboard`, `set_clipboard` |

## Underlying tech summary

| Library | Used for |
|---------|----------|
| `@nut-tree-fork/nut-js` | Mouse, keyboard, screen capture, clipboard |
| `active-win` | Detecting the focused window |
| `ps-list` | Listing / finding processes |
| `open` | Launching apps by path, opening files/URLs |
| `fast-glob` | Fast file search |
| PowerShell (`-EncodedCommand`) | Resolving app paths on Windows |
| Node `os` / `fs` / `child_process` | System info, file ops, shell commands |
| LangChain `tool()` + Zod | Tool definitions and argument validation |

## Extending

To add a tool:
1. Write the function in a `src/tools/*.js` module returning `{ success, result }`.
2. Register it in [`src/registry/tools.js`](../src/registry/tools.js) with a
   `tool()` wrapper, a clear `description`, and a Zod `schema`.
3. (Optional) Mention the new capability in the system prompt in
   [`src/agent/agentLoop.js`](../src/agent/agentLoop.js) so the agent knows when to
   use it.
