import { execFile } from "child_process";
import psList from "ps-list";
import { openApp } from "open";
import { runCommand } from "./terminalTools.js";
import { getSystemInfo } from "./systemTools.js";

function runPowerShell(script) {
    return new Promise((resolve) => {
        const encoded = Buffer.from(script, "utf16le").toString("base64");
        execFile(
            "powershell",
            ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            { timeout: 20000, windowsHide: true },
            (error, stdout, stderr) => {
                if (error) return resolve({ success: false, result: stderr || error.message });
                resolve({ success: true, result: (stdout || "").trim() });
            }
        );
    });
}

async function findRunning(appName, { fuzzy = false } = {}) {
    const target = appName.toLowerCase().replace(/\.exe$/, "");
    const procs = await psList();
    const exact = procs.filter(p => p.name.toLowerCase().replace(/\.exe$/, "") === target);
    if (exact.length || !fuzzy) return exact;
    return procs.filter(p => p.name.toLowerCase().includes(target));
}

export async function openApplication(appName) {
    const { platform } = getSystemInfo().result.system;

    if (platform === "darwin") return await runCommand(`open -a "${appName}"`);
    if (platform !== "win32") return await runCommand(`"${appName}" &`);

    const name = appName.replace(/'/g, "''");
    const script = `
$name = '${name}'
$exe = if ($name -match '\\.exe$') { $name } else { "$name.exe" }

# 1) PATH / registered command (notepad, calc, code, chrome...)
$path = (Get-Command $exe -ErrorAction SilentlyContinue).Source

# 2) App Paths registry — where most installed Win32 apps register their executable
if (-not $path) {
  $path = (Get-ItemProperty "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\$exe" -ErrorAction SilentlyContinue).'(default)'
}
if ($path) { "EXE|$($path.Trim('\"'))"; exit }

# 3) Start menu / UWP apps (fuzzy) — Store apps and shortcut-only GUI apps
$app = Get-StartApps | Where-Object { ($_.Name -replace '[\\s-]','') -like "*$($name -replace '[\\s-]','')*" } | Select-Object -First 1
if ($app) { "UWP|$($app.AppID)"; exit }

"NONE"
`.trim();

    const out = (await runPowerShell(script)).result || "";

    if (out.startsWith("EXE|")) {
        const exePath = out.slice(4);
        try {
            await openApp(exePath);
            return { success: true, result: `Opened ${appName} (${exePath})` };
        } catch (err) {
            return { success: false, result: `Found ${exePath} but failed to launch: ${err.message}` };
        }
    }

    if (out.startsWith("UWP|")) {
        const appId = out.slice(4);
        const res = await runCommand(`powershell -NoProfile -Command "Start-Process 'shell:AppsFolder\\${appId}'"`);
        return res.success
            ? { success: true, result: `Opened ${appName} (Store app)` }
            : res;
    }

    return { success: false, result: `Could not find an application matching "${appName}". It may not be installed.` };
}

export async function closeApplication(appName) {
    const { platform } = getSystemInfo().result.system;

    if (platform === "darwin") return await runCommand(`killall "${appName}"`);
    if (platform !== "win32") return await runCommand(`killall "${appName}"`);

    const matches = await findRunning(appName, { fuzzy: true });
    if (!matches.length) {
        return { success: false, result: `No running process found matching "${appName}".` };
    }

    const killed = [];
    const failed = [];
    for (const proc of matches) {
        try {
            process.kill(proc.pid);
            killed.push(`${proc.name} (PID ${proc.pid})`);
        } catch (err) {
            failed.push(`${proc.name} (PID ${proc.pid}): ${err.message}`);
        }
    }

    if (killed.length) {
        return {
            success: true,
            result: `Closed ${killed.join(", ")}${failed.length ? `. Failed: ${failed.join(", ")}` : ""}`,
        };
    }
    return { success: false, result: `Failed to close "${appName}": ${failed.join(", ")}` };
}
