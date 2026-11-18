"""The app model a desktop recording shows (see labctl.appmodel and docs/09-desktop.md).

- a window or dialog is a Screen with `surface: desktop`; its name is the window title as
  recorded (data-like parts masked), its AutomationId an alias; `desktop` holds the window
  locator, the menus, the grids, the tab order and the keyboard shortcuts seen;
- a control is an Element whose `selector` is the canonical UIA locator
  (`uia:AutomationId=txtCase;ControlType=Edit`); `uia` holds its properties, ancestor path,
  fallback locators and the UiPath, pywinauto and WinAppDriver forms;
- a grid (DataGrid or Table) with column headers is a candidate table, basis `assumed` (the
  weakest evidence, as the web path's HTML-grid inference); a detail dialog named after it adds
  the columns its fields name;
- the items of a drop-down list are a code list; a message box that answers an action is a rule.

Nothing is read from data: trees and steps hold only what labctl.desktop.privacy kept.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from labctl.appmodel import AppModel, Column, Element, Evidence, Rule, Screen, Stack, Table
from labctl.desktop import SURFACE, locator

INTERESTING = {"Button", "Edit", "Document", "CheckBox", "RadioButton", "ComboBox", "List",
               "DataGrid", "Table", "Tab", "TabItem", "MenuBar", "Hyperlink", "Tree", "Spinner",
               "Slider", "Group", "StatusBar", "ToolBar", "Text", "SplitButton", "Calendar",
               "MenuItem"}  # fmt: skip
ROWS = {"DataItem", "ListItem", "TreeItem"}
SQL_FOR = {"integer": "INTEGER", "decimal": "NUMERIC", "date": "DATE", "datetime": "TIMESTAMP",
           "boolean": "BOOLEAN", "text": "TEXT"}  # fmt: skip
FIELD_PREFIX = re.compile(r"^(txt|cbo|cmb|ddl|chk|cb|rb|rdo|opt|lst|dtp|num|nud|tb|edt|ed|fld|"
                          r"lbl|dgv|grd|grid)(?=[A-Z0-9_])")  # fmt: skip


SQL_RESERVED = {
    "case",
    "order",
    "user",
    "group",
    "table",
    "select",
    "where",
    "from",
    "index",
    "key",
    "check",
    "default",
    "references",
    "column",
    "end",
    "when",
    "values",
}


class Recording:
    """A desktop recording folder, read."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.session = json.loads((self.path / "desktop.json").read_text(encoding="utf-8"))
        self.steps = [json.loads(line) for line in
                      (self.path / "steps.jsonl").read_text(encoding="utf-8").splitlines()
                      if line.strip()]  # fmt: skip
        self._trees: dict[str, dict] = {}

    @property
    def id(self) -> str:
        return self.path.name

    def tree(self, rel: str | None) -> dict | None:
        if not rel:
            return None
        if rel not in self._trees:
            try:
                self._trees[rel] = json.loads((self.path / rel).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return self._trees[rel]

    def digest(self) -> str:
        h = hashlib.sha256()
        for f in sorted(self.path.rglob("*.json*")):
            h.update(f.relative_to(self.path).as_posix().encode())
            h.update(f.read_bytes())
        return "sha256:" + h.hexdigest()[:16]


def is_recording(path: Path) -> bool:
    path = Path(path)
    return path.is_dir() and (path / "desktop.json").is_file() and (path / "steps.jsonl").is_file()


def snake(text: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def field_name(aid: str | None, label: str | None = None) -> str | None:
    """`txtCustomer` -> `customer`; `cboStatus` -> `status`; a label when there is no id."""
    if aid and locator.stable_id(aid):
        name = snake(FIELD_PREFIX.sub("", aid))
        if name:
            return name
    if label:
        name = snake(re.sub(r"[&:*]", "", label))
        if name and not set(name) <= set("x9_"):
            return name
    return None


def singular(name: str) -> str:
    if name.endswith("ies") and len(name) > 4:
        return name[:-3] + "y"
    if name.endswith("s") and not name.endswith(("ss", "us", "is")) and len(name) > 3:
        return name[:-1]
    return name


def _merge_type(a: str, b: str) -> str:
    if a == b or b == "text":
        return a
    if a == "text":
        return b
    return "decimal" if {a, b} <= {"integer", "decimal"} else "text"


def _elements_of(tree: dict) -> list[tuple[dict, tuple, int]]:
    """Controls worth an Element: interactive or structural, never inside a data row."""
    out = []

    def walk(node: dict, ancestors: tuple, idx: int, data: bool) -> None:
        t = node.get("type")
        if t == "TitleBar":
            return  # the system menu and the caption buttons: the window manager's, not the app's
        if ancestors and not data and t in INTERESTING:
            if t != "Text" or node.get("name_is") in ("structure", "message"):
                out.append((node, ancestors, idx))
        inside = data or t in ROWS or (t in ("DataGrid", "Table", "List", "Tree"))
        counts: dict[str, int] = {}
        for child in node.get("children") or []:
            ct = child.get("type")
            n = counts.get(ct, 0)
            counts[ct] = n + 1
            c = dict(child)
            c["idx"] = n
            p = dict(node)
            p.setdefault("idx", idx)
            walk(c, (*ancestors, p), n, inside)

    walk(dict(tree), (), 0, False)
    return out


def _grid(node: dict) -> dict:
    """Columns (header names, structure) and the cell kinds per column of one grid."""
    headers: list[str] = []
    kinds: dict[int, list[str]] = {}
    rows = 0

    def header_names(n: dict) -> list[str]:
        names = []
        for c in n.get("children") or []:
            if c.get("type") in ("Header", "HeaderItem") and c.get("name_is") == "structure":
                names.append(c["name"])
            names += header_names(c) if c.get("type") == "Header" else []
        return names

    for child in node.get("children") or []:
        cells = [
            c
            for c in child.get("children") or []
            if c.get("type") in ("DataItem", "Custom", "Edit", "Text")
        ]
        found = header_names(child) if child.get("type") in ("Header", "Custom") else []
        if found and not headers:
            headers = found
            continue
        if cells:
            rows += 1
            for i, cell in enumerate(cells):
                if cell.get("kind"):
                    kinds.setdefault(i, []).append(cell["kind"])
    if not headers:
        headers = header_names(node)
    return {"headers": headers, "kinds": kinds, "rows": rows, "rows_reported": node.get("rows")}


def analyse(app: str, path: Path) -> AppModel:  # noqa: C901 - one pass over the recording
    rec = Recording(path)
    ev = f"desktop:{rec.id}"
    model = AppModel(app=app, evidence=[Evidence(id=ev, kind="desktop", ref=rec.digest())])
    procs = rec.session.get("processes") or []
    counts = Counter(s.get("process") for s in rec.steps if s.get("process"))
    if len(counts) > 1:
        model.problems.append(
            f"{ev}: the recording holds {len(counts)} applications ({', '.join(sorted(counts))}); "
            "split it first (`labctl desktop split`), one application per evidence set"
        )
    main = max(procs, key=lambda p: counts.get(p.get("process"), 0)) if procs else {}
    tech = dict(main.get("technology") or {})
    if tech.get("problem"):
        model.problems.append(f"{ev}: {tech.get('toolkit')}: {tech['problem']}")
    model.problems += [f"{ev}: recorder: {n}" for n in rec.session.get("notes") or []]
    model.stack = Stack(desktop={**{k: v for k, v in tech.items() if k != "evidence"},
                                 "process": main.get("process")},
                        detected=tech.get("evidence") or [], **{"from": [ev]})  # fmt: skip

    # -- windows ---------------------------------------------------------------------------------
    windows: dict[str, dict] = {}
    order: list[str] = []
    last_menu: dict[str, str] = {}
    for s in rec.steps:
        w = s.get("window") or {}
        key = w.get("selector")
        if not key:
            continue
        acc = windows.get(key)
        if acc is None:
            acc = windows[key] = {"window": w, "titles": Counter(), "trees": [], "steps": [],
                                  "menus": {}, "keys": [], "tab": [], "messages": []}  # fmt: skip
            order.append(key)
        if w.get("title"):
            acc["titles"][w["title"]] += 1
        acc["steps"].append(s)
        tree = rec.tree(s.get("tree"))
        if s.get("action") == "menu_open":
            parent = last_menu.get(key, "(menu)")
            items = acc["menus"].setdefault(parent, {})
            for node, _anc, _i in locator.walk(tree or {}):
                if node.get("type") == "MenuItem" and node.get("name_is") == "structure":
                    items.setdefault(node["name"], {"name": node["name"],
                                                    "accel": node.get("accel"),
                                                    "access_key": node.get("access_key")})  # fmt: skip
            continue
        if s.get("action") == "menu" and s.get("menu_path"):
            last_menu[key] = s["menu_path"].split(" > ")[-1]
        if tree is not None and s.get("action") != "window_close":
            acc["trees"].append(tree)
        if s.get("action") == "key" and s.get("key"):
            acc["keys"].append(s["key"])
            if s["key"].endswith("Tab") and s.get("focus_after"):
                acc["tab"].append(s["focus_after"])

    # A message box that opens after an action is that action's answer: a rule.
    by_key = {k: v for k, v in windows.items()}
    for n, s in enumerate(rec.steps):
        if s.get("action") != "window_open":
            continue
        tree = rec.tree(s.get("tree")) or {}
        if tree.get("cls") != "#32770" and not tree.get("message_box"):
            continue
        text = " ".join(node["name"] for node, _a, _i in locator.walk(tree)
                        if node.get("type") == "Text" and node.get("name_is") == "message")  # fmt: skip
        if not text:
            continue
        cause = next((p for p in reversed(rec.steps[:n]) if p.get("selector")
                      and p.get("action") in ("click", "key", "menu")), None)  # fmt: skip
        where = (cause or {}).get("window", {}).get("selector")
        acc = by_key.get(where)
        screen = acc and _title(acc)
        what = (
            (cause or {}).get("target", {}).get("name")
            or (cause or {}).get("selector")
            or "an action"
        )
        model.rules.append(Rule(
            text=f"after {what}: message box \"{tree.get('name') or ''}\": {text}",
            where=[screen] if screen else [], basis="observed", **{"from": [ev]}))  # fmt: skip
        if acc is not None:
            acc["messages"].append(text)
        box = by_key.get((s.get("window") or {}).get("selector"))
        if box is not None and screen:
            box["owner"] = screen

    # -- screens and elements --------------------------------------------------------------------
    tables: dict[str, dict] = {}
    combos: list[tuple[str, str, list[str]]] = []  # (screen, field, codes)
    for key in order:
        acc = windows[key]
        w = acc["window"]
        props = w.get("props") or {}
        name = _title(acc)
        boxed = any(t.get("message_box") for t in acc["trees"])
        if (props.get("ClassName") == "#32770" and not props.get("AutomationId")) or boxed:
            kind = "message box"
        else:
            kind = "window"
        elements: dict[str, Element] = {}
        aids: set[str] = set()
        actions: dict[str, list[str]] = {}
        for s in acc["steps"]:
            sel = s.get("selector")
            if sel and s.get("action") not in ("window_open", "window_close", "focus", "menu_open"):
                acts = actions.setdefault(sel, [])
                if s["action"] not in acts:
                    acts.append(s["action"])
        option_sets: dict[str, list[str]] = {}
        for s in acc["steps"]:
            if s.get("items") and s.get("selector"):
                option_sets.setdefault(s["selector"], [])
                option_sets[s["selector"]] += [
                    i for i in s["items"] if i not in option_sets[s["selector"]]
                ]
            if s.get("action") == "select" and s.get("item_is") == "code" and s.get("selector"):
                have = option_sets.setdefault(s["selector"], [])
                if s["item"] not in have:
                    have.append(s["item"])
        grids = []
        for tree in acc["trees"]:
            for node, ancestors, idx in _elements_of(tree):
                loc = locator.build(tree, node, ancestors, idx)
                sel = loc["selector"]
                if node.get("aid"):
                    aids.add(node["aid"])
                label = (
                    node.get("name") if node.get("name_is") in ("structure", "message") else None
                )
                el = elements.get(sel)
                if el is None:
                    uia = {"control_type": node.get("type"), "automation_id": node.get("aid"),
                           "name": label, "class_name": locator.normalise_class(node.get("cls")),
                           "framework_id": node.get("fw"), "index": idx, "path": loc["path"],
                           "matches": loc["matches"], "candidates": [c["selector"] for c in loc["candidates"]],
                           "enabled": node.get("enabled"), "visible": node.get("visible"),
                           "access_key": node.get("access_key"), "accel": node.get("accel"),
                           "rect": node.get("rect"),
                           **locator.forms(sel, w.get("selector"), _process_of(acc))}  # fmt: skip
                    for flag in ("password", "readonly"):
                        if node.get(flag):
                            uia[flag] = True
                    if loc.get("ambiguous"):
                        uia["ambiguous"] = True
                    if node.get("value") is not None and not node.get("password"):
                        uia["value_shapes"] = [node["value"]]
                    el = elements[sel] = Element(selector=sel, label=label, basis="observed",
                                                 uia={k: v for k, v in uia.items() if v is not None},
                                                 **{"from": [ev]})  # fmt: skip
                elif node.get("value") is not None and not node.get("password"):
                    shapes = el.uia.setdefault("value_shapes", [])
                    if node["value"] not in shapes and len(shapes) < 5:
                        shapes.append(node["value"])
                if node.get("type") in ("DataGrid", "Table"):
                    g = _grid(node)
                    if g["headers"] and sel not in {x["selector"] for x in grids}:
                        grids.append({"selector": sel, "columns": g["headers"],
                                      "rows_seen": g["rows"], "_kinds": g["kinds"]})  # fmt: skip
        for sel, acts in actions.items():
            el = elements.get(sel)
            if el is None:
                p = locator.parse(sel)
                el = elements[sel] = Element(selector=sel, basis="observed", **{"from": [ev]},
                                             label=p.get("Name"),
                                             uia={"control_type": p.get("ControlType"),
                                                  "automation_id": p.get("AutomationId"),
                                                  "note": "acted on, but not in a snapshot of its window"})  # fmt: skip
            el.actions = el.actions + [a for a in acts if a not in el.actions]
        for sel, opts in option_sets.items():
            if sel in elements:
                elements[sel].options = opts
                fname = field_name(locator.parse(sel).get("AutomationId"), elements[sel].label)
                if fname:
                    combos.append((name, fname, opts))
        menus = []
        for tree in acc["trees"][:1]:
            for node, anc, _i in locator.walk(tree):
                if any(a.get("type") == "TitleBar" for a in anc):
                    continue
                if node.get("type") == "MenuItem" and node.get("name_is") == "structure":
                    sub = acc["menus"].get(node["name"], {})
                    menus.append({"menu": node["name"], "access_key": node.get("access_key"),
                                  "items": list(sub.values())})  # fmt: skip
        known_menus = {m["menu"] for m in menus}
        for parent, items in acc["menus"].items():
            if parent not in known_menus:
                menus.append({"menu": parent, "items": list(items.values())})
        shortcuts = sorted({k for k in acc["keys"]} | {
            i["accel"] for m in menus for i in m["items"] if i.get("accel")})  # fmt: skip
        access = sorted({f"{e.label}: {e.uia.get('access_key')}" for e in elements.values()
                         if e.uia and e.uia.get("access_key") and e.label})  # fmt: skip
        desktop_owner = None
        if kind == "message box":
            # Its own screen, named after its title and kind (titles repeat: the sign-in dialog
            # and its message box are both "Sign in"); needed when the window that opens it is.
            desktop_owner = acc.get("owner")
            name = f"{name} (message box)"
        desktop = {"kind": kind, "window": {"selector": w.get("selector"), "props": props,
                                            "process": _process_of(acc),
                                            "uipath": locator.to_uipath(w.get("selector") or "",
                                                                        None, _process_of(acc))},  # fmt: skip
                   "titles": sorted(acc["titles"]), "menus": menus,
                   "grids": [{k: v for k, v in g.items() if not k.startswith("_")} for g in grids],
                   "tab_order_seen": list(dict.fromkeys(acc["tab"])),
                   "keys_seen": list(dict.fromkeys(acc["keys"])), "shortcuts": shortcuts,
                   "access_keys": access, "messages": acc["messages"],
                   "snapshots": len(acc["trees"]), "opened_from": desktop_owner}  # fmt: skip
        aliases = (
            [props["AutomationId"]]
            if props.get("AutomationId") and props["AutomationId"] != name
            else []
        )
        model.screens.append(Screen(name=name, aliases=aliases, basis="observed", **{"from": [ev]},
                                    surface=SURFACE, desktop=desktop, ids=sorted(aids),
                                    elements=list(elements.values()),
                                    snapshots=len(acc["trees"])))  # fmt: skip
        for g in grids:
            gid = locator.parse(g["selector"]).get("AutomationId")
            base = field_name(gid) or snake(name) or "grid"
            tname = singular(base)
            if tname in SQL_RESERVED:  # `case`, `order`, `user`: a table name the DDL cannot use
                tname = base if base not in SQL_RESERVED else f"{base}_record"
            t = tables.setdefault(tname, {"columns": {}, "screens": {name}, "grid": g["selector"]})
            for i, col in enumerate(g["columns"]):
                cname = snake(col)
                if not cname:
                    continue
                kinds = g["_kinds"].get(i, [])
                kind = None
                for k in kinds:
                    kind = k if kind is None else _merge_type(kind, k)
                t["columns"].setdefault(cname, {"type": kind or "text", "from": "grid"})

    # A detail window named after a table (its title's first word) adds its fields as columns.
    for screen in model.screens:
        word = singular(snake((screen.name or "").split(" ")[0]))
        word = next((t for t in tables if singular(t) == word or t == word), word)
        if word not in tables or screen.name in tables[word]["screens"]:
            continue
        cols = tables[word]["columns"]
        for el in screen.elements:
            t = (el.uia or {}).get("control_type")
            if t not in ("Edit", "ComboBox", "CheckBox", "Spinner", "RadioButton") or (
                el.uia or {}
            ).get("password"):
                continue
            fname = field_name((el.uia or {}).get("automation_id"), el.label)
            if not fname or fname in cols:
                continue
            kind = "boolean" if t == "CheckBox" else "text"
            cols[fname] = {"type": kind, "from": f"fields of {screen.name}"}
    for tname, t in tables.items():
        names = list(t["columns"])
        if not names:
            continue
        first = names[0]
        pk = re.search(r"(^|_)(id|no|number|code|ref|key)$", first) is not None
        model.tables.append(Table(name=tname, basis="assumed", **{"from": [ev]}, columns=[
            Column(name=c, type=SQL_FOR.get(i["type"], "TEXT"), nullable=not (pk and c == first),
                   primary_key=pk and c == first) for c, i in t["columns"].items()]))  # fmt: skip
        model.problems.append(
            f"{ev}: table `{tname}` is inferred from the grid {t['grid']} and the fields of the "
            "windows around it (weakest evidence): names and types are guesses"
        )
    for screen_name, fname, opts in combos:
        target = next((t for t in tables if fname in tables[t]["columns"]), None)
        model.codes[f"{target or snake(screen_name) or 'window'}.{fname}"] = opts
    if not model.screens:
        model.problems.append(f"{ev}: no window of the application was recorded")
    return model


def _title(acc: dict) -> str:
    props = (acc["window"] or {}).get("props") or {}
    if acc["titles"]:
        return acc["titles"].most_common(1)[0][0]
    return props.get("AutomationId") or props.get("Name") or "window"


def _process_of(acc: dict) -> str | None:
    for s in acc["steps"]:
        if s.get("process"):
            return s["process"]
    return None
