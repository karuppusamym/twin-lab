"""Step labels a portal may receive, so that a label is never a customer's data.

A flow step is `{"n", "action", "control", "label", "screen"}`:

- desktop: the control's AutomationId when it passes `plain()`; else its Name, only for a
  control type whose name is the application's own words (`NAMED`) and that passes `plain()`;
  else no label. Never the name of a Text, ListItem, DataItem, TreeItem, Hyperlink, Custom or
  Document. The screen is the window's AutomationId, else the part of its title after the last
  " - " when that passes `plain()`.
- web: the element's id or name from the selector when it passes `plain()`; the control is the
  tag. Never link text, row text or a quoted text selector. The screen is the origin and a route
  template: a path segment with a digit, or longer than 24 characters, becomes `{id}`; no query,
  fragment or user information.

`plain(text)`: at most 40 characters, no digit, no `@`, not a shape the recorder wrote
(`X-9999`: a run of X/x/9 with punctuation), and not empty.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from labctl.workstation.levels import origin_of

NAMED = {"Button", "CheckBox", "RadioButton", "MenuItem", "Menu", "MenuBar", "TabItem", "Tab",
         "ComboBox", "Edit", "Window", "Group", "ToolBar", "SplitButton", "HeaderItem"}  # fmt: skip
NEVER = {"Text", "ListItem", "DataItem", "TreeItem", "Hyperlink", "Custom", "Document", "Image"}
SHAPE = re.compile(r"^[Xx9\W_]+$")
UIA_PART = re.compile(r"(\w+)=([^;]*)")
WEB_ID = re.compile(r"#([A-Za-z_][\w-]*)")
WEB_ATTR = re.compile(
    r"\[(?:name|id|data-testid|data-test|aria-label)\s*=\s*['\"]?([^'\"\]]+)['\"]?\]"
)
WEB_TAG = re.compile(r"^\s*([a-z][a-z0-9-]*)")
ACTIONS = {
    "click", "dblclick", "double_click", "right_click", "type", "select", "toggle", "expand",
    "collapse", "menu", "menu_open", "key", "keypress", "check", "uncheck", "submit", "goto",
    "download", "window_open", "window_close", "focus", "tab-open", "tab-switch", "tab-close",
}  # fmt: skip


def plain(text: object) -> bool:
    s = str(text or "").strip()
    if not s or len(s) > 40 or "@" in s or any(c.isdigit() for c in s):
        return False
    return not SHAPE.match(s)


def _uia(selector: object) -> dict:
    s = str(selector or "")
    if s.startswith("uia:"):
        s = s[4:]
    return {k: v for k, v in UIA_PART.findall(s)}


def route_template(url: str) -> str:
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return ""
    segs = []
    for seg in (parts.path or "/").split("/"):
        if not seg:
            continue
        segs.append("{id}" if any(c.isdigit() for c in seg) or len(seg) > 24 or "@" in seg
                    else seg)  # fmt: skip
    return "/" + "/".join(segs)


def desktop_step(step: dict) -> dict:
    target = step.get("target") or {}
    props = dict(target.get("props") or {})
    sel = _uia(target.get("selector") or step.get("selector"))
    control = str(props.get("ControlType") or sel.get("ControlType") or "") or None
    label = None
    aid = props.get("AutomationId") or sel.get("AutomationId")
    if aid and plain(aid):
        label = aid
    elif control in NAMED and control not in NEVER:
        name = props.get("Name") or sel.get("Name")
        if plain(name):
            label = name
    window = step.get("window") or {}
    wprops = dict(window.get("props") or {})
    wsel = _uia(window.get("selector"))
    screen = wprops.get("AutomationId") or wsel.get("AutomationId")
    if not (screen and plain(screen)):
        title = str(window.get("title") or "")
        part = title.rsplit(" - ", 1)[-1].strip() if title else ""
        screen = part if plain(part) else None
    return {"control": control if control and plain(control) else None, "label": label,
            "screen": screen}  # fmt: skip


def web_step(step: dict) -> dict:
    sel = str(step.get("selector") or "")
    if ">>>" in sel:  # inside a frame: the element's own part
        sel = sel.rsplit(">>>", 1)[-1]
    label = None
    if "text=" not in sel and "has-text" not in sel and ":text" not in sel:
        for rx in (WEB_ID, WEB_ATTR):
            m = rx.search(sel)
            if m and plain(m.group(1)):
                label = m.group(1)
                break
    m = WEB_TAG.match(sel)
    control = m.group(1) if m and plain(m.group(1)) else None
    url = str(step.get("url") or "")
    origin = origin_of(url)
    return {"control": control, "label": label,
            "screen": f"{origin}{route_template(url)}" if origin else None}  # fmt: skip


def label_step(step: dict, n: int) -> dict:
    """One flow step of the portal's references."""
    action = str(step.get("action") or "")
    out = {"n": n, "action": action if action in ACTIONS else "other"}
    facts = desktop_step(step) if step.get("surface") == "desktop" else web_step(step)
    out.update({k: v for k, v in facts.items() if v})
    if step.get("key") and str(step["key"]) in {
        "Enter", "Tab", "Esc", "Escape", "Delete", "Backspace", "F1", "F2", "F3", "F4", "F5",
        "F6", "F7", "F8", "F9", "F10", "F11", "F12",
    }:  # fmt: skip
        out["key"] = str(step["key"])
    return out
