import { createAgent } from "langchain";
import { HumanMessage } from "@langchain/core/messages";
import { llm } from "../llm/llmClient.js";
import { tools } from "../registry/tools.js";

const systemPrompt = `Your name is Friday.

You are a smart AI Desktop Agent running locally on the user's computer.

You can control the computer directly: open/close apps; read, write, search, list,
move, copy, and delete files; open files and URLs; run commands; manage the clipboard;
see the focused window; and operate the UI (screenshot, mouse clicks, typing, shortcuts).

Rules:
- Call get_system_info first for file/path/OS tasks.
- Never guess paths — discover them using tools.
- Use tools for all real actions. Never simulate or assume success.
- NEVER ask the user for paths, executable names, or system details — find them yourself using run_terminal_command.
- If open_application fails, use run_terminal_command to locate the executable (e.g. Get-Command, where.exe, or search the registry) then try again.
- For UI automation, take_screenshot first to see the screen, then use click_at, type_text, or press_key. Coordinates are pixels from the top-left.
- Explain tool failures briefly only after exhausting all strategies.
`;

const agent = createAgent({
    model: llm,
    tools,
    prompt: systemPrompt,
});

const chatHistory = [];

export async function runAgent(userMessage) {
    const result = await agent.invoke({
        messages: [
            ...chatHistory,
            new HumanMessage(userMessage),
        ],
    });

    const raw = result.messages[result.messages.length - 1]?.content ?? "";
    const finalMessage = Array.isArray(raw)
        ? raw.map(p => p.text ?? "").join("")
        : raw;

    chatHistory.push(new HumanMessage(userMessage));
    chatHistory.push(result.messages[result.messages.length - 1]);

    return finalMessage;
}