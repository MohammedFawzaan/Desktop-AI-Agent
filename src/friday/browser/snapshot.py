# ruff: noqa: E501  (embedded JavaScript)
"""Turn a web page into a short numbered list of what can be clicked or typed into, plus its text."""

from __future__ import annotations

from typing import Any

# Runs inside the page. Tags interactive elements with data-friday-id so they can be found again.
SNAPSHOT_JS = r"""
({limit, find, textLimit}) => {
  const SEL = 'a[href],button,input:not([type=hidden]),textarea,select,summary,' +
    '[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],' +
    '[role=option],[role=switch],[role=combobox],[role=textbox],[role=searchbox],' +
    '[contenteditable=""],[contenteditable=true],h1,h2,h3';
  for (const el of document.querySelectorAll('[data-friday-id]')) el.removeAttribute('data-friday-id');
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const labelOf = el => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean)
        .map(e => e.innerText).join(' ');
      if (clean(t)) return t;
    }
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
      if (el.labels && el.labels.length) return Array.from(el.labels).map(l => l.innerText).join(' ');
      if (tag === 'INPUT' && ['submit', 'button', 'reset'].includes(el.type)) return el.value;
      return el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '';
    }
    const img = el.querySelector('img[alt]');
    return el.innerText || el.getAttribute('title') || (img && img.getAttribute('alt')) || '';
  };
  const kindOf = el => {
    const tag = el.tagName.toLowerCase();
    if (/^h[1-3]$/.test(tag)) return 'heading';
    const role = el.getAttribute('role');
    if (role) return role;
    if (tag === 'a') return 'link';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (['submit', 'button', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'checkbox' || t === 'radio') return t;
      return t === 'text' ? 'input' : t + ' input';
    }
    if (tag === 'textarea') return 'textarea';
    if (tag === 'select') return 'select';
    if (el.isContentEditable) return 'editor';
    return tag;
  };
  const visible = el => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const typeable = k => k === 'input' || k === 'textarea' || k === 'editor' || k.endsWith(' input') ||
    k === 'textbox' || k === 'searchbox' || k === 'combobox';
  const needle = (find || '').toLowerCase();
  const items = [];
  const seenLinks = new Set();
  let matched = 0, next = 1;
  for (const el of document.querySelectorAll(SEL)) {
    if (!visible(el)) continue;
    const kind = kindOf(el);
    const name = clean(labelOf(el)).slice(0, 120);
    let value = '';
    const toggleOrButton = ['checkbox', 'radio', 'submit', 'button', 'reset', 'image'].includes(el.type);
    if ((el.tagName === 'INPUT' && !toggleOrButton) || el.tagName === 'TEXTAREA') {
      value = el.type === 'password' ? (el.value ? '********' : '') : (el.value || '');
    } else if (el.tagName === 'SELECT') {
      value = el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '';
    }
    if (!name && !value && !typeable(kind)) continue;
    if (needle && !(name + ' ' + value).toLowerCase().includes(needle)) continue;
    if (kind === 'link') {
      const key = name + '|' + el.getAttribute('href');
      if (seenLinks.has(key)) continue;
      seenLinks.add(key);
    }
    matched++;
    if (items.length >= limit) continue;
    const id = next++;
    el.setAttribute('data-friday-id', String(id));
    const item = {id, kind, name, value: clean(value).slice(0, 80)};
    if (el.disabled) item.disabled = true;
    if (kind === 'checkbox' || kind === 'radio' || kind === 'switch') {
      const aria = el.getAttribute('aria-checked');
      item.checked = aria ? aria === 'true' : !!el.checked;
    }
    if (kind === 'link') item.href = (el.getAttribute('href') || '').slice(0, 100);
    items.push(item);
  }
  const body = document.body ? document.body.innerText : '';
  const text = body.replace(/[ \t]+/g, ' ').replace(/\n\s*\n\s*\n+/g, '\n\n').trim();
  return {title: document.title, url: location.href, items, matched,
          text: text.slice(0, textLimit), textLength: text.length};
}
"""

_PW_KEYS = {
    "ctrl": "Control", "control": "Control", "alt": "Alt", "shift": "Shift", "win": "Meta",
    "enter": "Enter", "return": "Enter", "esc": "Escape", "escape": "Escape", "tab": "Tab",
    "space": "Space", "backspace": "Backspace", "delete": "Delete", "del": "Delete",
    "insert": "Insert", "home": "Home", "end": "End", "pageup": "PageUp", "pgup": "PageUp",
    "pagedown": "PageDown", "pgdn": "PageDown", "up": "ArrowUp", "down": "ArrowDown",
    "left": "ArrowLeft", "right": "ArrowRight", "plus": "+", "minus": "-",
}  # fmt: skip


class KeysError(ValueError):
    pass


def to_playwright_keys(spec: str) -> list[str]:
    """'ctrl+l, enter' -> ['Control+l', 'Enter'] (Playwright's key names)."""
    combos: list[str] = []
    for chunk in spec.split(","):
        names: list[str] = []
        for raw in chunk.split("+"):
            part = raw.strip().lower()
            if not part:
                continue
            if part in _PW_KEYS:
                names.append(_PW_KEYS[part])
            elif len(part) == 1:
                names.append(part)
            elif part[0] == "f" and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
                names.append(part.upper())
            else:
                raise KeysError(f"Unknown key '{raw.strip()}'")
        if names:
            combos.append("+".join(names))
    if not combos:
        raise KeysError("No keys given")
    return combos


def describe_item(item: dict[str, Any]) -> str:
    parts = [f"[{item['id']}] {item['kind']}"]
    if item.get("name"):
        parts.append(f'"{item["name"]}"')
    if item.get("value") and item.get("value") != item.get("name"):
        parts.append(f'= "{item["value"]}"')
    if "checked" in item:
        parts.append("[checked]" if item["checked"] else "[unchecked]")
    if item.get("disabled"):
        parts.append("[disabled]")
    if item.get("href") and not str(item["href"]).startswith(("javascript:", "#")):
        parts.append(f"-> {item['href']}")
    return " ".join(parts)


def format_snapshot(data: dict[str, Any], *, find: str = "", with_text: bool = True) -> str:
    items: list[dict[str, Any]] = list(data.get("items", []))
    matched = int(data.get("matched", len(items)))
    lines = [f'Page: "{data.get("title", "")}" {data.get("url", "")}']
    scope = f" matching '{find}'" if find else ""
    summary = f"{len(items)} of {matched} elements{scope}"
    if matched > len(items):
        summary += " (use find=... to narrow)"
    lines.append(summary)
    lines.extend(describe_item(item) for item in items)
    text = str(data.get("text", ""))
    if with_text and text:
        lines.append("Page text:")
        lines.append(text)
        hidden = int(data.get("textLength", len(text))) - len(text)
        if hidden > 0:
            lines.append(f"...[{hidden} more characters; use find=... or scroll]")
    return "\n".join(lines)
