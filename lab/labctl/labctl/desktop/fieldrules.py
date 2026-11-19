"""Field rules and window behaviour of a desktop application: `apps/<app>/rules.json`.

Per window, per input control, what the recordings show without reading data:

- on open: enabled, visible, read-only, password, its value's shape;
- what Win32 reports (labctl.desktop.winlook): maximum length, multi-line, digits only, upper or
  lower case, a combo box that can or cannot be typed in, the default button;
- the items of a drop-down list (a code list);
- validation: every message box that answered an action, with the shapes the window's fields
  had when the action was taken, and the same action taken later without a message box (what
  was then accepted); "X is required" messages make X required.

The application is not decompiled: a rule the recordings do not show is not here, and the
brief says so. Plain Python.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from labctl.desktop import locator
from labctl.desktop import look as dlook

FILE = "rules.json"
INPUTS = {"Edit", "Document", "ComboBox", "CheckBox", "RadioButton", "Spinner", "Slider",
          "Calendar", "List"}  # fmt: skip
REQUIRED = re.compile(r"^\s*(?:the\s+)?([\w][\w ]{0,40}?)\s+(?:is|are)\s+(?:required|mandatory)",
                      re.I)  # fmt: skip


def _state(tree: dict) -> dict[str, dict]:
    """selector -> what an input shows (shape, toggle, enabled) in a window tree."""
    out = {}
    for node, anc, idx in locator.walk(tree):
        if node.get("type") not in INPUTS or not anc:
            continue
        if any(a.get("type") in ("DataItem", "ListItem", "DataGrid", "Table") for a in anc):
            continue
        sel = locator.build(tree, node, anc, idx)["selector"]
        st = {}
        if "value" in node:
            st["shape"] = node["value"]
            st["length"] = len(node["value"] or "")
        if node.get("toggle"):
            st["toggle"] = node["toggle"]
        if node.get("enabled") is False:
            st["enabled"] = False
        out[sel] = st
    return out


def _controls(tree: dict, model_screen) -> list[dict]:
    opts = {e.selector: e.options for e in (model_screen.elements if model_screen else [])
            if e.options}  # fmt: skip
    out = []
    for node, anc, idx in locator.walk(tree):
        t = node.get("type")
        if not anc or (t not in INPUTS and t != "Button"):
            continue
        if any(a.get("type") in ("DataItem", "ListItem", "DataGrid", "Table", "TitleBar",
                                 "ComboBox") for a in anc):  # fmt: skip
            continue
        sel = locator.build(tree, node, anc, idx)["selector"]
        w32 = node.get("win32") or {}
        item: dict = {"selector": sel, "type": t}
        if node.get("name") and node.get("name_is") != "data":
            item["label"] = node["name"]
        if t == "Button":
            if w32.get("default_button"):
                item["default_button"] = True
                out.append(item)
            continue
        item["on_open"] = {k: v for k, v in (("enabled", node.get("enabled", True)),
                                             ("visible", node.get("visible", True)),
                                             ("read_only", node.get("readonly", False)),
                                             ("password", bool(node.get("password"))))}  # fmt: skip
        if node.get("value") is not None:
            item["on_open"]["shape"] = node["value"]
        if node.get("toggle"):
            item["on_open"]["toggle"] = node["toggle"]
        for k in ("max_length", "multiline", "digits_only", "upper_case", "lower_case",
                  "combo_style"):  # fmt: skip
            if w32.get(k) is not None:
                item[k] = w32[k]
        if sel in opts:
            item["items"] = opts[sel]
        if t == "ComboBox" and node.get("items_are") == "data":
            item["items"] = "data (not kept)"
        out.append(item)
    return out


def build(recs, model) -> dict:
    wins = dlook.collect(recs)
    screens = {(s.desktop or {}).get("window", {}).get("selector"): s for s in model.screens}
    windows = []
    for key, win in wins.items():
        if not win.snaps:
            continue
        tree = win.snaps[0][2]
        entry = {"window": key, "id": dlook.slug(win.window, win.kind), "kind": win.kind,
                 "controls": _controls(tree, screens.get(key)), "validation": [],
                 "keys": []}  # fmt: skip
        windows.append(entry)
    by_key = {w["window"]: w for w in windows}
    for rec in recs:
        steps = rec.steps
        last_tree: dict[str, dict] = {}
        chosen: dict[str, dict[str, str]] = {}  # window -> combo box -> code chosen
        typed: dict[str, dict[str, str]] = {}  # window -> field -> shape typed since it opened
        for n, s in enumerate(steps):
            key = (s.get("window") or {}).get("selector")
            if s.get("action") == "select" and s.get("item_is") == "code" and key:
                chosen.setdefault(key, {})[s.get("selector")] = s.get("item")
            if s.get("action") == "type" and key and s.get("typed_shape") is not None:
                # What was typed is exact; a snapshot read after a batch may show a later state.
                typed.setdefault(key, {})[s.get("selector")] = s["typed_shape"]
            if s.get("action") == "window_open" and key:
                chosen.pop(key, None)
                typed.pop(key, None)
            if s.get("action") == "key" and s.get("invoked") and key in by_key:
                k = {"key": s["key"], "presses": s["invoked"]}
                if k not in by_key[key]["keys"]:
                    by_key[key]["keys"].append(k)
                    for c in by_key[key]["controls"]:
                        if c["selector"] == s["invoked"] and s["key"] == "Enter":
                            c["default_button"] = True
            if s.get("action") in ("click", "key", "menu") and s.get("selector") and key in by_key:
                before = last_tree.get(key)
                answer = None
                for m in steps[n + 1 : n + 4]:
                    if m.get("action") == "window_open":
                        t = rec.tree(m.get("tree")) or {}
                        if t.get("cls") == "#32770" or t.get("message_box"):
                            text = " ".join(nd["name"] for nd, _a, _i in locator.walk(t)
                                            if nd.get("type") == "Text"
                                            and nd.get("name_is") == "message")  # fmt: skip
                            answer = {"message_box": t.get("name") or "", "text": text}
                        break
                    if m.get("action") in ("click", "type", "key", "menu", "select", "toggle"):
                        break
                closes = any(m.get("action") == "window_close"
                             and (m.get("window") or {}).get("selector") == key
                             for m in steps[n + 1 : n + 3])  # fmt: skip
                if answer or closes or s.get("selector") in {
                    v.get("action_on") for v in by_key[key]["validation"]}:  # fmt: skip
                    fields = _state(before) if before else {}
                    for sel, item in (chosen.get(key) or {}).items():
                        fields.setdefault(sel, {})["item"] = item
                    for sel, shape in (typed.get(key) or {}).items():
                        fields.setdefault(sel, {}).update(shape=shape, length=len(shape),
                                                          typed=True)  # fmt: skip
                    by_key[key]["validation"].append({
                        "step": s["seq"], "recording": rec.id, "action": s["action"],
                        "action_on": s.get("invoked") or s.get("selector"),
                        "fields": fields,
                        "outcome": answer or ("the window closed" if closes else "no message"),
                    })  # fmt: skip
                    if answer:
                        req = REQUIRED.match(answer["text"])
                        if req:
                            label = req.group(1).strip().lower()
                            for c in by_key[key]["controls"]:
                                if (c.get("label") or "").lower() == label:
                                    c["required"] = True
            t = rec.tree(s.get("tree"))
            if t is not None and key and s.get("action") not in ("window_close", "menu_open"):
                last_tree[key] = t
    return {"format": "twinlab-desktop-rules/1", "windows": windows,
            "unknown": "what the application checks that no recorded action showed (no message "
                       "box, no change seen) is not known; the application was not decompiled"}  # fmt: skip


def write(root: Path, app_id: str, model, recs=None) -> dict | None:
    recs = dlook.recordings(root, app_id) if recs is None else recs
    if not recs:
        return None
    doc = build(recs, model)
    (Path(root) / "apps" / app_id / FILE).write_text(
        json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")  # fmt: skip
    return doc


def _flags(c: dict) -> str:
    out = []
    o = c.get("on_open") or {}
    if o.get("read_only"):
        out.append("read-only")
    if o.get("enabled") is False:
        out.append("disabled on open")
    if o.get("visible") is False:
        out.append("hidden on open")
    if o.get("password"):
        out.append("password")
    if c.get("required"):
        out.append("**required**")
    if c.get("max_length"):
        out.append(f"max {c['max_length']} chars" + (" (the toolkit's default)"
                                                     if c["max_length"] in (32767, 2147483647)
                                                     else ""))  # fmt: skip
    for k, t in (("multiline", "multi-line"), ("digits_only", "digits only"),
                 ("upper_case", "upper case"), ("lower_case", "lower case")):  # fmt: skip
        if c.get(k):
            out.append(t)
    if c.get("combo_style"):
        out.append({"dropdown-list": "drop-down list (no typing)", "dropdown": "editable drop-down",
                    "simple": "simple combo"}.get(c["combo_style"], c["combo_style"]))  # fmt: skip
    if isinstance(c.get("items"), list):
        out.append("items " + ", ".join(f"`{i}`" for i in c["items"]))
    if c.get("default_button"):
        out.append("default button (Enter)")
    return "; ".join(out)


def brief_section(doc: dict | None, app: str) -> str:
    if not doc:
        return ""
    lines = ["## Field rules and validation seen\n",
             f"From `{app}/{FILE}`. Reproduce each; a rule not listed was not observed (the "
             "application was not decompiled): keep the toolkit's default and list it as an "
             "assumption.\n"]  # fmt: skip
    for w in doc["windows"]:
        rows = [(c["selector"], _flags(c)) for c in w["controls"] if _flags(c)]
        if not rows and not w["validation"]:
            continue
        lines.append(f"### `{w['id']}`\n")
        for sel, f in rows:
            lines.append(f"- `{sel}`: {f}")
        for v in w["validation"]:
            fields = ", ".join(
                f"`{locator.parse(k).get('AutomationId') or k}`="
                + (f"`{x['item']}`" if "item" in x else f"`{x['shape']}` ({x['length']} chars)"
                   if "shape" in x else x.get("toggle", ""))
                for k, x in v["fields"].items() if "shape" in x or x.get("toggle") or "item" in x
            )  # fmt: skip
            out = v["outcome"]
            what = (f'message box "{out["message_box"]}": {out["text"]}' if isinstance(out, dict)
                    else out)  # fmt: skip
            lines.append(f"- step {v['step']}: {v['action']} `{v['action_on']}` with {fields or 'no fields'}"
                         f" → {what}")  # fmt: skip
        lines.append("")
    return "\n".join(lines) + "\n"
