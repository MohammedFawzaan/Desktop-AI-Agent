import path from "path";
import {
    mouse, keyboard, screen,
    Point, Button, Key, FileType,
} from "@nut-tree-fork/nut-js";
import { getSystemInfo } from "./systemTools.js";

mouse.config.autoDelayMs = 100;
mouse.config.mouseSpeed = 2000;
keyboard.config.autoDelayMs = 5;

const KEY_MAP = {
    ctrl: Key.LeftControl, control: Key.LeftControl,
    alt: Key.LeftAlt,
    shift: Key.LeftShift,
    win: Key.LeftSuper, super: Key.LeftSuper, cmd: Key.LeftSuper, meta: Key.LeftSuper,
    enter: Key.Enter, return: Key.Enter,
    tab: Key.Tab, esc: Key.Escape, escape: Key.Escape,
    space: Key.Space, spacebar: Key.Space,
    backspace: Key.Backspace, delete: Key.Delete, del: Key.Delete,
    up: Key.Up, down: Key.Down, left: Key.Left, right: Key.Right,
    home: Key.Home, end: Key.End,
};

function resolveKey(name) {
    const k = name.toLowerCase().trim();
    if (KEY_MAP[k]) return KEY_MAP[k];
    if (/^[a-z]$/.test(k)) return Key[k.toUpperCase()];     // a-z
    if (/^[0-9]$/.test(k)) return Key[`Num${k}`];           // 0-9
    if (/^f([1-9]|1[0-2])$/.test(k)) return Key[k.toUpperCase()]; // F1-F12
    return null;
}

export async function takeScreenshot(filePath) {
    try {
        const { paths } = getSystemInfo().result;
        const target = filePath
            ? path.resolve(filePath)
            : path.join(paths.desktop, `screenshot-${Date.now()}.png`);

        const dir = path.dirname(target);
        const name = path.basename(target).replace(/\.png$/i, "");

        const saved = await screen.capture(name, FileType.PNG, dir);
        return { success: true, result: `Screenshot saved to ${saved}` };
    } catch (error) {
        return { success: false, result: error.message };
    }
}

export async function clickAt(x, y, button = "left", double = false) {
    try {
        await mouse.setPosition(new Point(x, y));
        const btn = button === "right" ? Button.RIGHT : button === "middle" ? Button.MIDDLE : Button.LEFT;
        if (double) await mouse.doubleClick(btn);
        else await mouse.click(btn);
        return { success: true, result: `${double ? "Double-" : ""}${button} clicked at (${x}, ${y})` };
    } catch (error) {
        return { success: false, result: error.message };
    }
}

export async function typeText(text) {
    try {
        await keyboard.type(text);
        return { success: true, result: `Typed: "${text}"` };
    } catch (error) {
        return { success: false, result: error.message };
    }
}

export async function pressKey(keys) {
    try {
        const list = Array.isArray(keys) ? keys : [keys];
        const resolved = list.map(resolveKey);

        const bad = list.filter((_, i) => resolved[i] == null);
        if (bad.length) return { success: false, result: `Unknown key(s): ${bad.join(", ")}` };

        await keyboard.pressKey(...resolved);
        await keyboard.releaseKey(...resolved.reverse());
        return { success: true, result: `Pressed: ${list.join("+")}` };
    } catch (error) {
        return { success: false, result: error.message };
    }
}
