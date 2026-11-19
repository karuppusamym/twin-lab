"""Recorded steps, seed rows and replay values of a desktop application, made together.

- `steps/<recording>.json`: the recorded steps in order (action, window, control, typed shape,
  item chosen, key, menu path, what the window showed after it: rows per grid, message box
  text), with the step's picture without data in `steps/<recording>/<seq>.png` when it has one.
- `db/seed.sql` and `db/seed.json`: synthetic rows for the tables of `db/schema.sql`, as many as
  the grids showed, each cell in the shape the recording showed in that place; values that are
  codes (a status a drop-down list offers) are those codes.
- `steps/<recording>.values.yaml`: the synthetic value `labctl desktop verify` types at each
  type step (`--value` options), chosen with the seed so the recorded outcomes happen again:
  a search key that finds as many rows as the recording showed after it; a password that is
  refused where the recording showed a message box and accepted where the window closed.

A desktop app reads its database directly: the recorder sees screens, not SQL. Which columns a
grid shows and the shapes of the values are seen; the queries, other tables and the matching
rule of a search are not, and are written as assumptions.

Plain Python.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, timedelta
from pathlib import Path

import yaml

from labctl.desktop import locator
from labctl.desktop import look as dlook

LETTERS = "BCDFGHKLMNPRSTVW"
VOWELS = "aeiou"


def synth(shape: str, n: int = 0, salt: int = 0) -> str:
    """A synthetic value of a shape (`X-9999` -> `K-1001`), deterministic in n and salt."""
    out, d, k = [], 0, n * 7 + salt * 3
    for ch in shape or "":
        if ch == "X":
            out.append(LETTERS[k % len(LETTERS)])
            k += 1
        elif ch == "x":
            out.append(VOWELS[k % 5] if len(out) % 2 else LETTERS[k % len(LETTERS)].lower())
            k += 1
        elif ch == "9":
            out.append(str((n + d + 1) % 10) if d else str(1 + salt % 9))
            d += 1
        else:
            out.append(ch)
    return "".join(out)


def _grids(tree: dict) -> dict[str, dict]:
    """selector -> {"rows": n, "cells": [[shape per column] per row], "headers": [...]}"""
    out = {}
    for node, anc, idx in locator.walk(tree):
        if node.get("type") not in ("DataGrid", "Table") or not anc:
            continue
        sel = locator.build(tree, node, anc, idx)["selector"]
        headers, rows = [], []
        for child in node.get("children") or []:
            kids = child.get("children") or []
            hs = [k["name"] for k in kids if k.get("type") in ("Header", "HeaderItem")
                  and k.get("name_is") == "structure"]  # fmt: skip
            if hs and not headers:
                headers = hs
                continue
            cells = [
                k.get("value") for k in kids if k.get("type") in ("DataItem", "Edit", "Custom")
            ]
            if cells:
                rows.append(cells)
        out[sel] = {"rows": len(rows), "cells": rows, "headers": headers}
    return out


def steps_doc(rec, root: Path, app_id: str) -> dict:
    out_dir = Path(root) / "apps" / app_id / "steps" / rec.id
    shutil.rmtree(out_dir, ignore_errors=True)
    steps = []
    for s in rec.steps:
        item = {"n": s["seq"], "action": s["action"],
                "window": (s.get("window") or {}).get("selector"),
                "title": (s.get("window") or {}).get("title")}  # fmt: skip
        for k in ("selector", "typed_shape", "password", "item", "item_is", "items", "key",
                  "invoked", "focus_after", "menu_path", "state", "container", "ambiguous"):  # fmt: skip
            if s.get(k) not in (None, "", []):
                item[k] = s[k]
        tree = rec.tree(s.get("tree"))
        if s.get("tree_late"):
            item["tree_late"] = True
        if tree is not None and not s.get("tree_late"):
            g = {sel: v["rows"] for sel, v in _grids(tree).items()}
            if g:
                item["rows_after"] = g
            if tree.get("cls") == "#32770" or tree.get("message_box"):
                item["message"] = " ".join(n["name"] for n, _a, _i in locator.walk(tree)
                                           if n.get("type") == "Text"
                                           and n.get("name_is") == "message")  # fmt: skip
        pic = None if s.get("tree_late") else s.get("picture")
        if pic and (rec.path / pic).is_file():
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rec.path / pic, out_dir / f"{s['seq']:04d}.png")
            item["picture"] = f"{rec.id}/{s['seq']:04d}.png"
        steps.append(item)
    return {"recording": rec.id,
            "about": "The recorded steps of this application in order. `window` and `selector` are "
                     "UI Automation locators (docs in BRIEF.md); `typed_shape` is the shape of what "
                     "was typed (letters X/x, digits 9; never the value); `password` marks typing "
                     "in a password field (not even its shape is kept); `item` is the code chosen "
                     "in a drop-down list; `rows_after` the rows each grid showed after the step; "
                     "`message` a message box's text; `picture` the window after the step, data "
                     "painted over. A step's tree and picture are read after a batch of actions: they "
                     "can show a later state (a value typed at a later step); `typed_shape` and "
                     "the step order are exact. The values the replay types are in "
                     "<recording>.values.yaml.",
            "steps": steps}  # fmt: skip


def _search_steps(rec) -> list[dict]:
    """Type steps followed (before the next type) by a grid showing another number of rows."""
    out = []
    rows_now: dict[str, int] = {}
    pending = None
    for s in rec.steps:
        tree = rec.tree(s.get("tree"))
        if s.get("action") == "type" and s.get("typed_shape") and not s.get("password"):
            pending = s
        if tree is not None:
            for sel, g in _grids(tree).items():
                before = rows_now.get(sel)
                rows_now[sel] = g["rows"]
                if pending is not None and before is not None and g["rows"] != before:
                    out.append({"step": pending, "grid": sel, "rows": g["rows"], "before": before,
                                "after_step": s["seq"]})  # fmt: skip
                    pending = None
    return out


def plan(recs, model) -> dict:
    """The seed rows and the replay values, together (module docstring)."""
    first_grid: dict[str, dict] = {}
    for rec in recs:
        for s in rec.steps:
            tree = rec.tree(s.get("tree"))
            for sel, g in _grids(tree).items() if tree else []:
                if sel not in first_grid or g["rows"] > first_grid[sel]["rows"]:
                    first_grid[sel] = g
    searches = [x for rec in recs for x in _search_steps(rec)]
    codes = {k.split(".", 1)[-1]: v for k, v in (model.codes or {}).items()}
    tables, values, notes = {}, {}, []
    for t in model.tables:
        grid_sel = next((g for g in first_grid if any(
            dmodel_field(h) == t.columns[0].name for h in first_grid[g]["headers"][:1])), None)  # fmt: skip
        g = first_grid.get(grid_sel) if grid_sel else None
        n = max(g["rows"] if g else 0, 1)
        headers = [dmodel_field(h) for h in (g["headers"] if g else [])]
        search = next((x for x in searches if x["grid"] == grid_sel), None)
        key = None
        if search:
            key = synth(search["step"]["typed_shape"], 0, 5)
            values[(search["step"].get("selector"), search["step"]["seq"])] = (
                key, f"finds {search['rows']} of the {n} seeded rows (the recording showed "
                f"{search['rows']} after step {search['after_step']})")  # fmt: skip
            notes.append(f"The search typed at step {search['step']['seq']} "
                         f"(`{search['step'].get('selector')}`) is assumed to match the first "
                         f"column by its beginning or anywhere in it (contains): the seed puts "
                         f"`{key}` at the start of {search['rows']} of the {n} rows' "
                         f"`{t.columns[0].name}` and nowhere else.")  # fmt: skip
        rows = []
        for i in range(n):
            row = {}
            cells = g["cells"][i] if g and i < len(g["cells"]) else []
            for c in t.columns:
                col = headers.index(c.name) if c.name in headers else None
                shape = cells[col] if col is not None and col < len(cells) else None
                row[c.name] = _value(c, shape, i, codes.get(c.name), key if c is t.columns[0]
                                     else None, search["rows"] if search else None, n)  # fmt: skip
            rows.append(row)
        tables[t.name] = rows
    # Passwords: refused where a message box followed, accepted where the window closed.
    users = []
    for rec in recs:
        user_shape = None
        for n_, s in enumerate(rec.steps):
            if s.get("action") == "type" and not s.get("password") and s.get("typed_shape") and (
                re.search(r"user|login|name", s.get("selector") or "", re.I)):  # fmt: skip
                user_shape = s["typed_shape"]
                values.setdefault((s["selector"], s["seq"]), (synth(user_shape, 0, 2),
                                                              "the seeded user's name"))  # fmt: skip
            if s.get("action") == "type" and s.get("password"):
                nxt = rec.steps[n_ + 1 : n_ + 5]
                refused = any(m.get("action") == "window_open" and (
                    (m.get("window") or {}).get("props") or {}).get("ClassName") == "#32770"
                    for m in nxt)  # fmt: skip
                value = "Wrong-Pass-1" if refused else "Demo-Pass-1"
                values[(s["selector"], s["seq"])] = (
                    value, "refused (the recording showed a message box)" if refused
                    else "accepted: the seeded user's password")  # fmt: skip
        if user_shape:
            users.append({"user_name": synth(user_shape, 0, 2), "password": "Demo-Pass-1"})
    if users:
        tables["app_users"] = users[:1]
        notes.append("Sign-in: the recording shows a refused and an accepted password; the seed "
                     "adds `app_users` (user_name, password) with the one account the replay "
                     "signs in with. How the real application checks a password is not known.")  # fmt: skip
    for rec in recs:  # every other type step: a synthetic value of its shape
        for s in rec.steps:
            if s.get("action") == "type" and not s.get("password") and s.get("typed_shape"):
                values.setdefault((s.get("selector"), s["seq"]),
                                  (synth(s["typed_shape"], s["seq"], 1),
                                   "a synthetic value of the recorded shape"))  # fmt: skip
    return {"tables": tables, "values": values, "notes": notes}


def dmodel_field(header: str) -> str:
    from labctl.desktop import model as dmodel

    return dmodel.snake(header or "")


def _other_key(key: str) -> str:
    """A value of the key's shape that the key does not match (another first letter or
    digit)."""
    for i, ch in enumerate(key):
        if ch.isalpha():
            alt = "Q" if ch.upper() != "Q" else "Z"
            return key[:i] + (alt if ch.isupper() else alt.lower()) + key[i + 1 :]
        if ch.isdigit():
            return key[:i] + str((int(ch) + 5) % 10) + key[i + 1 :]
    return "Z" + key[1:]


def _value(col, shape, i, code_values, key, matches, n):
    t = (col.type or "TEXT").upper()
    if key is not None and shape:
        tail = synth(shape, i, 7)[len(key) :]
        base = key if i < (matches or 0) else _other_key(key)
        return base + tail
    if code_values:
        from labctl import sanitize

        same = [c for c in code_values if shape and sanitize.mask(c) == shape]
        return (same or code_values)[i % len(same or code_values)]
    if t in ("DATE",) or (shape and re.fullmatch(r"9999-99-99", shape)):
        return (date(2026, 10, 1) + timedelta(days=3 * i)).isoformat()
    if t == "BOOLEAN":
        return False
    if t in ("INTEGER", "NUMERIC"):
        return i + 1
    if shape:
        return synth(shape, i, 4)
    return synth("Xxxxx xxxx", i, 4)


def _sql_value(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int | float):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def write(root: Path, app_id: str, model, recs=None) -> dict | None:
    recs = dlook.recordings(root, app_id) if recs is None else recs
    if not recs:
        return None
    app = Path(root) / "apps" / app_id
    (app / "steps").mkdir(parents=True, exist_ok=True)
    (app / "db").mkdir(parents=True, exist_ok=True)
    for rec in recs:
        (app / "steps" / f"{rec.id}.json").write_text(
            json.dumps(steps_doc(rec, root, app_id), indent=1, ensure_ascii=False), encoding="utf-8")  # fmt: skip
    p = plan(recs, model)
    lines = [f"-- Seed data for the {app_id} demo app. Generated by labctl brief from the desktop "
             "recordings, with the replay values (steps/*.values.yaml);",
             "-- do not edit by hand. Synthetic values only, in the shapes the recordings showed. "
             "Load exactly these rows, once, into the empty tables.", "-- Dialect: sqlite."]  # fmt: skip
    for note in p["notes"]:
        lines.append(f"-- {note}")
    if "app_users" in p["tables"]:
        lines.append("CREATE TABLE IF NOT EXISTS app_users (user_name TEXT PRIMARY KEY, "
                     "password TEXT NOT NULL);")  # fmt: skip
    for tname, rows in p["tables"].items():
        lines.append(f"\n-- {tname}: {len(rows)} row(s)")
        for r in rows:
            lines.append(f'INSERT INTO "{tname}" (' + ", ".join(f'"{k}"' for k in r) + ") VALUES ("
                         + ", ".join(_sql_value(v) for v in r.values()) + ");")  # fmt: skip
    (app / "db" / "seed.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (app / "db" / "seed.json").write_text(json.dumps(p["tables"], indent=1, default=str),
                                          encoding="utf-8")  # fmt: skip
    for rec in recs:
        entries = []
        for s in rec.steps:
            got = p["values"].get((s.get("selector"), s["seq"]))
            if got is None or s.get("action") != "type":
                continue
            aid = locator.parse(s.get("selector") or "").get("AutomationId")
            entries.append({"n": s["seq"], "action": "type", "field": s.get("selector"),
                            "automation_id": aid, "shape": s.get("typed_shape"),
                            "password": bool(s.get("password")) or None, "value": got[0],
                            "why": got[1]})  # fmt: skip
        doc = {"recording": rec.id, "app": app_id,
               "about": "The synthetic value `labctl desktop verify` types at each type step "
                        "(pass each as `--value <AutomationId>=<value>`, in this order), chosen "
                        "with db/seed.sql so the recorded outcomes happen again. Never a value a "
                        "person typed.",
               "verify_options": [f"--value {e['automation_id']}={e['value']}" for e in entries
                                  if e["automation_id"]],
               "steps": [{k: v for k, v in e.items() if v is not None} for e in entries]}  # fmt: skip
        (app / "steps" / f"{rec.id}.values.yaml").write_text(
            "# Replay values (generated by labctl brief with db/seed.sql). Synthetic only.\n"
            + yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True), encoding="utf-8")  # fmt: skip
    return p


def brief_section(p: dict | None, app: str, recs) -> str:
    if not p:
        return ""
    lines = ["## Recorded steps, seed data and replay values\n",
             f"- `{app}/steps/<recording>.json`: every recorded step in order, with what each "
             "window showed after it (rows per grid, message box text) and a picture of the "
             "window after it (`steps/<recording>/<n>.png`, data painted over). Your app must let "
             "these steps happen in this order with these locators.",
             f"- `{app}/db/seed.sql` (and `seed.json`): load **exactly** these rows into the empty "
             "tables on first start, and no other rows: the replay types the values in "
             f"`{app}/steps/<recording>.values.yaml` and expects the recorded outcomes on these "
             "rows.",
             "- A desktop application reads its database directly: what the real one stores and "
             "how it queries is **not known** beyond the columns its grids show and the fields "
             "its windows edit. Keep the data access plain.\n"]  # fmt: skip
    for t, rows in p["tables"].items():
        lines.append(f"- `{t}`: {len(rows)} row(s)")
    for note in p["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines) + "\n"
