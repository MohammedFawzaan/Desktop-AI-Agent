import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { openApplication, closeApplication } from "../tools/osTools.js";
import { searchFiles, writeFile, openFile } from "../tools/fileTools.js";
import { runCommand } from "../tools/terminalTools.js";
import { getSystemInfo, getRunningProcesses } from "../tools/systemTools.js";
import { takeScreenshot, clickAt, typeText, pressKey } from "../tools/automationTools.js";

const stringify = (result) => JSON.stringify(result);

export const tools = [
    tool(async ({ appName }) => stringify(await openApplication(appName)), {
        name: "open_application",
        description: "Open any desktop application on the operating system.",
        schema: z.object({
            appName: z.string().describe("The executable name of the application to open"),
        }),
    }),

    tool(async ({ appName }) => stringify(await closeApplication(appName)), {
        name: "close_application",
        description: "Close any desktop application currently running on the operating system.",
        schema: z.object({
            appName: z.string().describe("The executable name of the application to close"),
        }),
    }),

    tool(async ({ pattern, directory }) => stringify(await searchFiles(pattern, directory)), {
        name: "search_files",
        description: "Search for files by name or pattern. Plain filenames like 'report.pdf' are searched recursively. If no directory is given and nothing is found in home, it auto-searches desktop, documents, downloads, and pictures.",
        schema: z.object({
            pattern: z.string().describe("Filename or glob pattern. Plain names ('report.pdf') search recursively; '*.pdf' matches by extension; '*resume*' fuzzy-matches names."),
            directory: z.string().optional().describe("Where to search: 'desktop', 'documents', 'downloads', or a full path. Omit to search broadly."),
        }),
    }),

    tool(async ({ filePath, content }) => stringify(await writeFile(filePath, content)), {
        name: "write_file",
        description: "Create or edit a text file with provided content.",
        schema: z.object({
            filePath: z.string().describe("Full or relative path of the file"),
            content: z.string().describe("Content to write into the file"),
        }),
    }),

    tool(async ({ target }) => stringify(await openFile(target)), {
        name: "open_file",
        description: "Open a file or URL in its default application (e.g. a PDF in the PDF viewer, an image in the photo viewer, or a webpage in the browser). Accepts a full path, a filename (searched in common folders), or an http(s) URL.",
        schema: z.object({
            target: z.string().describe("File path, filename, or URL to open"),
        }),
    }),

    tool(async ({ command }) => stringify(await runCommand(command)), {
        name: "run_terminal_command",
        description: "Run terminal or shell commands on the operating system and return output.",
        schema: z.object({
            command: z.string().describe("The shell command to execute"),
        }),
    }),

    tool(() => stringify(getSystemInfo()), {
        name: "get_system_info",
        description: "Returns OS, username, file system paths, CPU, memory. Call this first whenever you need paths or OS details.",
        schema: z.object({}),
    }),

    tool(async () => stringify(await getRunningProcesses()), {
        name: "get_running_processes",
        description: "Returns all running processes grouped by app name with instance counts and PIDs. Use when the user asks what is running or wants to find/kill a process.",
        schema: z.object({}),
    }),

    tool(async ({ filePath }) => stringify(await takeScreenshot(filePath)), {
        name: "take_screenshot",
        description: "Capture the current screen and save it as a PNG. Use to see what is currently on screen before interacting with the UI.",
        schema: z.object({
            filePath: z.string().optional().describe("Where to save the PNG. Omit to save to the desktop with a timestamped name."),
        }),
    }),

    tool(async ({ x, y, button, double }) => stringify(await clickAt(x, y, button, double)), {
        name: "click_at",
        description: "Move the mouse to screen coordinates (x, y) and click. Coordinates are in pixels from the top-left of the primary screen.",
        schema: z.object({
            x: z.number().describe("X coordinate in pixels"),
            y: z.number().describe("Y coordinate in pixels"),
            button: z.enum(["left", "right", "middle"]).optional().describe("Which mouse button (default: left)"),
            double: z.boolean().optional().describe("Set true for a double-click"),
        }),
    }),

    tool(async ({ text }) => stringify(await typeText(text)), {
        name: "type_text",
        description: "Type text into the currently focused application as if typed on the keyboard.",
        schema: z.object({
            text: z.string().describe("The text to type"),
        }),
    }),

    tool(async ({ keys }) => stringify(await pressKey(keys)), {
        name: "press_key",
        description: "Press a key or key combination (chord). Examples: ['enter'], ['ctrl','s'], ['alt','f4'], ['win','d'], ['ctrl','shift','t'].",
        schema: z.object({
            keys: z.array(z.string()).describe("Keys to press together. Modifiers: ctrl, alt, shift, win. Others: enter, tab, esc, space, backspace, delete, up/down/left/right, home, end, a-z, 0-9, f1-f12."),
        }),
    }),
];
