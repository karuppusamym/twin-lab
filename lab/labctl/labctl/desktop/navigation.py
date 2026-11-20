"""The navigation map of a desktop application: `apps/<app>/navigation.json`.

Windows (main windows, dialogs, message boxes), the menus and sub-menus seen with their items,
tabs, MDI children, and every move between windows the recordings show: which step opened a
window (the user's action just before it opened), which closed it, and whether the window it
came from was blocked while it was open (modal). What was never opened is listed as not seen.

Plain Python: reads recordings (labctl.desktop.model.Recording), writes one file.
"""

from __future__ import annotations

import json
from pathlib import Path

from labctl.desktop import locator
from labctl.desktop import look as dlook

FILE = "navigation.json"
USER_ACTIONS = {"click", "double_click", "right_click", "key", "menu", "select", "toggle", "type"}


def _cause(steps: list[dict], n: int) -> dict | None:
    """The user's action that a window event at index n answers: the nearest user step before
    it (within 3 s), whatever window it was in."""
    for p in reversed(steps[max(0, n - 6) : n]):
        if p.get("action") in USER_ACTIONS and p.get("selector"):
            return p
    return None


def _ref(step: dict | None) -> dict | None:
    if step is None:
        return None
    out = {"step": step.get("seq"), "action": step.get("action"), "control": step.get("selector"),
           "window": (step.get("window") or {}).get("selector")}  # fmt: skip
    if step.get("key"):
        out["key"] = step["key"]
    if step.get("menu_path"):
        out["menu"] = step["menu_path"]
    if step.get("invoked"):
        out["invoked"] = step["invoked"]
    return out


def build(recs) -> dict:
    wins = dlook.collect(recs)
    windows: dict[str, dict] = {}
    for key, win in wins.items():
        windows[key] = {"id": dlook.slug(win.window, win.kind), "window": key, "kind": win.kind,
                        "titles": sorted(win.titles), "opened_by": [], "closed_by": [],
                        "first": False}  # fmt: skip
        frame = (win.snaps[0][2].get("frame") if win.snaps else None) or {}
        if frame:
            windows[key]["frame"] = {k: frame.get(k) for k in
                                     ("resizable", "minimize_box", "maximize_box", "dialog_frame",
                                      "placed") if k in frame}  # fmt: skip
        tabs, mdi = [], []
        for _r, _s, tree, _p in win.snaps:
            for node, _anc, _i in locator.walk(tree):
                if node.get("type") == "TabItem" and node.get("name_is") == "structure":
                    if node["name"] not in tabs:
                        tabs.append(node["name"])
                if (node.get("cls") or "").startswith("MDIClient") and "MDI" not in mdi:
                    mdi.append("MDI")
        if tabs:
            windows[key]["tabs"] = tabs
        if mdi:
            windows[key]["mdi_container"] = True
    moves, menus = [], {}
    for rec in recs:
        steps = rec.steps
        open_now: list[str] = []
        for n, s in enumerate(steps):
            key = (s.get("window") or {}).get("selector")
            act = s.get("action")
            if act == "window_open" and key:
                cause = _cause(steps, n)
                if not open_now and key in windows:
                    windows[key]["first"] = True
                if cause is not None and key in windows:
                    ref = _ref(cause)
                    if ref not in windows[key]["opened_by"]:
                        windows[key]["opened_by"].append(ref)
                    moves.append({"from": ref["window"], "to": key, "how": "opens", **{
                        k: v for k, v in ref.items() if k != "window"}, "recording": rec.id})  # fmt: skip
                open_now.append(key)
            elif act == "window_close" and key:
                cause = _cause(steps, n)
                if cause is not None and key in windows:
                    ref = _ref(cause)
                    if ref not in windows[key]["closed_by"]:
                        windows[key]["closed_by"].append(ref)
                    moves.append({"from": key, "to": None, "how": "closes", **{
                        k: v for k, v in ref.items() if k != "window"}, "recording": rec.id})  # fmt: skip
                if key in open_now:
                    open_now.remove(key)
            elif act == "menu_open" and key:
                tree = rec.tree(s.get("tree")) or {}
                prev = next((p for p in reversed(steps[:n]) if p.get("action") == "menu"), None)
                path = (prev or {}).get("menu_path") or "menu"
                items = []
                for node, _anc, _i in locator.walk(tree):
                    if node.get("type") == "MenuItem" and node.get("name_is") == "structure":
                        item = {"name": node["name"]}
                        if node.get("accel"):
                            item["shortcut"] = node["accel"]
                        if node.get("access_key"):
                            item["access_key"] = node["access_key"]
                        if node.get("enabled") is False:
                            item["enabled"] = False
                        if node.get("expand") in ("collapsed", "expanded"):
                            item["submenu"] = True
                        items.append(item)
                menus.setdefault(key, {})[path] = items
    # Where an item chosen from a menu led (the next window event after it).
    for m in moves:
        if m.get("menu"):
            m["note"] = f"menu {m['menu']}"
    # Modal: the window it came from was disabled in a snapshot taken while it was open.
    for key, w in windows.items():
        if w["kind"] == "message box":
            w["modal"] = True
            tree = wins[key].snaps[0][2] if key in wins and wins[key].snaps else {}
            buttons = []
            for node, _a, _i in locator.walk(tree):
                if node.get("type") == "Image" and (node.get("win32") or {}).get("icon"):
                    w["icon"] = node["win32"]["icon"]
                if node.get("type") == "Button" and node.get("name_is") == "structure" and not any(
                        a.get("type") == "TitleBar" for a in _a):  # fmt: skip
                    buttons.append(node["name"] + (" (default)" if (node.get("win32") or {}).get(
                        "default_button") else ""))  # fmt: skip
            if buttons:
                w["buttons"] = buttons
            w["note"] = ("a standard message box (MessageBox.Show with its owner): UI Automation "
                         "lists it as a child of the window that owns it")  # fmt: skip
            continue
        owners = {o["window"] for o in w["opened_by"] if o.get("window") and o["window"] != key}
        blocked = None
        for owner in owners:
            win = wins.get(owner)
            if win and any(t.get("enabled") is False for _r, _s, t, _p in win.snaps):
                blocked = True
        w["modal"] = (blocked if blocked else
                      "not seen (the window under it was never read while it was open)")  # fmt: skip
    menu_list = []
    for key, by_path in menus.items():
        for path, items in by_path.items():
            menu_list.append({"window": key, "menu": path, "items": items})
    for key, win in wins.items():
        bar = []
        for _r, _s, tree, _p in win.snaps[:1]:
            for node, anc, _i in locator.walk(tree):
                if any(a.get("type") == "TitleBar" for a in anc):
                    continue
                if (
                    node.get("type") == "MenuItem"
                    and node.get("name_is") == "structure"
                    and any(a.get("type") == "MenuBar" for a in anc)
                ):
                    bar.append(node["name"])
        opened = {m["menu"].split(" > ")[-1] for m in menu_list if m["window"] == key}
        for name in bar:
            if name not in opened:
                menu_list.append({"window": key, "menu": name, "items": None,
                                  "note": "never opened in the recordings: its items are not "
                                          "known"})  # fmt: skip
    return {"format": "twinlab-desktop-navigation/1", "windows": list(windows.values()),
            "menus": menu_list, "moves": moves,
            "not_seen": "windows, menus and items the recordings never opened; what the "
                        "buttons never pressed do"}  # fmt: skip


def write(root: Path, app_id: str, recs=None) -> dict | None:
    recs = dlook.recordings(root, app_id) if recs is None else recs
    if not recs:
        return None
    doc = build(recs)
    path = Path(root) / "apps" / app_id / FILE
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return doc


def brief_section(doc: dict | None, app: str) -> str:
    if not doc:
        return ""
    lines = ["## Windows and how they open\n",
             f"From `{app}/{FILE}` (every move the recordings show; the step numbers are those of "
             f"`{app}/steps/*.json`).\n", "| Window | Kind | Modal | Opened by | Closed by |",
             "|---|---|---|---|---|"]  # fmt: skip

    def refs(items):
        return (
            "; ".join(
                f"step {r['step']}: {r['action']} `{r['control']}`"
                + (f" ({r['key']})" if r.get("key") else "")
                + (f" ({r['menu']})" if r.get("menu") else "")
                for r in items
            )
            or "-"
        )

    for w in doc["windows"]:
        opened = "the application starts with it" if w.get("first") and not w["opened_by"] else (
            refs(w["opened_by"]) or "-")  # fmt: skip
        modal = "yes" if w.get("modal") is True else str(w.get("modal") or "-")
        kind = w["kind"] + (f" ({w['icon']} icon; {', '.join(w.get('buttons') or [])})"
                            if w.get("icon") else "")  # fmt: skip
        lines.append(f"| `{w['id']}` ({', '.join(w['titles'])}) | {kind} | {modal} | "
                     f"{opened} | {refs(w['closed_by']) or '-'} |")  # fmt: skip
    lines.append("")
    for m in doc["menus"]:
        items = ", ".join(i["name"] + (f" ({i['shortcut']})" if i.get("shortcut") else "")
                          for i in m["items"] or []) or "*never opened: items not known*"  # fmt: skip
        lines.append(f"- Menu **{m['menu']}** in `{dlook.slug({'selector': m['window']}, '')}`: "
                     f"{items}")  # fmt: skip
    lines.append("")
    return "\n".join(lines) + "\n"
