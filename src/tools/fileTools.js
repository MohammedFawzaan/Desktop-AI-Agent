import fg from "fast-glob";
import path from "path";
import fs from "fs/promises";
import open from "open";
import { getSystemInfo } from "./systemTools.js";

const IGNORE = [
    "**/node_modules/**",
    "**/AppData/**",
    "**/.git/**",
    "**/$Recycle.Bin/**",
    "**/Windows/**",
    "**/.cache/**",
];

const MAX_READ_BYTES = 100 * 1024;

const toPosix = (p) => p.replace(/\\/g, "/");

async function globIn(dir, pattern) {
    return fg(pattern, {
        cwd: toPosix(dir),
        absolute: true,
        caseSensitiveMatch: false,
        suppressErrors: true,
        ignore: IGNORE,
        dot: false,
    });
}

function resolveDirectory(directory) {
    const { paths } = getSystemInfo().result;

    if (!directory) return paths.home;
    if (path.isAbsolute(directory)) return directory;

    const normalized = directory.replace(/\\/g, "/");
    const parts = normalized.split("/");
    const key = parts[0].toLowerCase().trim();

    if (paths[key]) {
        return parts.length > 1
            ? path.join(paths[key], ...parts.slice(1))
            : paths[key];
    }

    return path.join(paths.home, directory);
}

function resolvePath(target) {
    return path.isAbsolute(target)
        ? target
        : path.join(resolveDirectory(path.dirname(target)), path.basename(target));
}

async function resolveExistingPath(target) {
    const direct = resolvePath(target);
    try {
        await fs.access(direct);
        return direct;
    } catch {
        const found = await globIn(resolveDirectory(undefined), `**/${path.basename(target)}`);
        return found[0] || null;
    }
}

export async function searchFiles(pattern, directory) {
    try {
        const deepPattern = (!pattern.includes("/") && !pattern.includes("**"))
            ? `**/${pattern}`
            : toPosix(pattern);

        const primaryDir = resolveDirectory(directory);
        let files = await globIn(primaryDir, deepPattern);

        if (files.length === 0 && !directory) {
            const { paths } = getSystemInfo().result;
            const fallbacks = [paths.desktop, paths.documents, paths.downloads, paths.pictures];
            const results = await Promise.all(fallbacks.map(d => globIn(d, deepPattern)));
            files = [...new Set(results.flat())];
        }

        if (files.length === 0) {
            return {
                success: true,
                result: `No files matching "${pattern}" found. Try a different pattern or specify a directory.`,
            };
        }

        return {
            success: true,
            result: `Found ${files.length} file(s):\n${files.slice(0, 25).join("\n")}${files.length > 25 ? `\n...and ${files.length - 25} more` : ""}`,
        };
    } catch (error) {
        return { success: false, result: error.message };
    }
}

export async function writeFile(filePath, content) {
    try {
        const dir = path.dirname(filePath);
        const fileName = path.basename(filePath);
        const resolvedDir = resolveDirectory(dir);
        const resolvedPath = path.join(resolvedDir, fileName);

        await fs.mkdir(resolvedDir, { recursive: true });
        await fs.writeFile(resolvedPath, content, "utf8");

        return {
            success: true,
            result: `File written successfully at ${resolvedPath}`
        };

    } catch (error) {
        return {
            success: false,
            result: error.message
        };
    }
}

export async function openFile(target) {
    try {
        if (/^https?:\/\//i.test(target)) {
            await open(target);
            return { success: true, result: `Opened URL ${target}` };
        }

        let resolved = path.isAbsolute(target)
            ? target
            : path.join(resolveDirectory(path.dirname(target)), path.basename(target));

        try {
            await fs.access(resolved);
        } catch {
            const found = await globIn(resolveDirectory(undefined), `**/${path.basename(target)}`);
            if (!found.length) {
                return { success: false, result: `File not found: ${target}` };
            }
            resolved = found[0];
        }

        await open(resolved);
        return { success: true, result: `Opened ${resolved}` };
    } catch (error) {
        return { success: false, result: error.message };
    }
}

export async function readFile(filePath) {
    try {
        const resolved = await resolveExistingPath(filePath);
        if (!resolved) return { success: false, result: `File not found: ${filePath}` };

        const stat = await fs.stat(resolved);
        if (stat.isDirectory()) return { success: false, result: `${resolved} is a directory, not a file.` };

        const content = await fs.readFile(resolved, "utf8");
        if (content.length > MAX_READ_BYTES) {
            return {
                success: true,
                result: `${resolved} (showing first ${MAX_READ_BYTES} of ${content.length} chars):\n${content.slice(0, MAX_READ_BYTES)}`,
            };
        }
        return { success: true, result: `${resolved}:\n${content}` };
    } catch (error) {
        return { success: false, result: error.message };
    }
}

export async function listDirectory(directory) {
    try {
        const resolved = resolveDirectory(directory);
        const entries = await fs.readdir(resolved, { withFileTypes: true });

        if (entries.length === 0) return { success: true, result: `${resolved} is empty.` };

        const folders = entries.filter(e => e.isDirectory()).map(e => `[dir]  ${e.name}`);
        const files = entries.filter(e => e.isFile()).map(e => `       ${e.name}`);
        const listing = [...folders, ...files];

        return {
            success: true,
            result: `${resolved} (${entries.length} item(s)):\n${listing.slice(0, 100).join("\n")}${listing.length > 100 ? `\n...and ${listing.length - 100} more` : ""}`,
        };
    } catch (error) {
        return { success: false, result: error.message };
    }
}

export async function deleteFile(filePath) {
    try {
        const resolved = await resolveExistingPath(filePath);
        if (!resolved) return { success: false, result: `File not found: ${filePath}` };

        await fs.rm(resolved, { recursive: true, force: true });
        return { success: true, result: `Deleted ${resolved}` };
    } catch (error) {
        return { success: false, result: error.message };
    }
}

export async function moveFile(source, destination) {
    try {
        const src = await resolveExistingPath(source);
        if (!src) return { success: false, result: `Source not found: ${source}` };

        const dest = resolvePath(destination);
        await fs.mkdir(path.dirname(dest), { recursive: true });

        try {
            await fs.rename(src, dest);
        } catch (err) {
            if (err.code !== "EXDEV") throw err;
            await fs.copyFile(src, dest);
            await fs.rm(src, { force: true });
        }
        return { success: true, result: `Moved ${src} -> ${dest}` };
    } catch (error) {
        return { success: false, result: error.message };
    }
}

export async function copyFile(source, destination) {
    try {
        const src = await resolveExistingPath(source);
        if (!src) return { success: false, result: `Source not found: ${source}` };

        const dest = resolvePath(destination);
        await fs.mkdir(path.dirname(dest), { recursive: true });
        await fs.copyFile(src, dest);
        return { success: true, result: `Copied ${src} -> ${dest}` };
    } catch (error) {
        return { success: false, result: error.message };
    }
}
