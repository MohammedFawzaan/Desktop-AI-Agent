import { app, BrowserWindow, ipcMain, session } from "electron";
import { fileURLToPath } from "url";
import { runAgent } from "./src/agent/agentLoop.js";
import path from "path";
import dotenv from "dotenv";

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const createWindow = () => {
    const win = new BrowserWindow({
        width: 800,
        height: 600,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js')
        }
    });

    // Start at 100% zoom, then allow keyboard zoom controls
    win.webContents.on('did-finish-load', () => win.webContents.setZoomFactor(1));

    win.webContents.on('before-input-event', (event, input) => {
        if (!input.control || input.type !== 'keyDown') return;
        const wc = win.webContents;
        const current = wc.getZoomFactor();

        if (input.key === '=' || input.key === '+') {          // Ctrl + =/+  → zoom in
            wc.setZoomFactor(Math.min(current + 0.1, 3));
            event.preventDefault();
        } else if (input.key === '-') {                         // Ctrl + -    → zoom out
            wc.setZoomFactor(Math.max(current - 0.1, 0.5));
            event.preventDefault();
        } else if (input.key === '0') {                         // Ctrl + 0    → reset
            wc.setZoomFactor(1);
            event.preventDefault();
        }
    });

    // In dev mode load from Vite dev server, in prod load the built file
    if (process.env.NODE_ENV === 'development') {
        win.loadURL('http://localhost:5173');
    } else {
        win.loadFile('dist/index.html');
    }
};

app.whenReady().then(async () => {
    try {
        session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
            callback(permission === 'media');
        });

        session.defaultSession.setPermissionCheckHandler((_wc, permission) => {
            return permission === 'media';
        });

        createWindow();

        ipcMain.handle('transcribe-audio', async (_event, base64Audio, mimeType) => {
            const { GoogleGenerativeAI } = await import("@google/generative-ai");
            const genAI = new GoogleGenerativeAI(process.env.GOOGLE_GEMINI_API_KEY);
            const model = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });
            const result = await model.generateContent([
                { inlineData: { data: base64Audio, mimeType } },
                "Transcribe this audio exactly as spoken. Return only the transcript, nothing else.",
            ]);
            return result.response.text().trim();
        });

        ipcMain.on('user-message', async (event, text) => {
            try {
                const response = await runAgent(text);

                console.log("\nFinal Response:\n");
                console.log(response);

                // Send the LLM's response back to the React UI
                event.reply('agent-response', response);
            } catch (error) {
                console.error(error);
                event.reply('agent-response', "Sorry I encountered an error.");
            }
        });

        app.on('activate', () => {
            if (BrowserWindow.getAllWindows().length === 0) createWindow();
        });

    } catch (error) {
        console.error("Error:", error);
    }
});

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
});