"""The build documents of a desktop application: `apps/<app>/spec/`.

The web path writes them per page (labctl.builder.docs); a desktop application gets the same
set per window, from the files the desktop package carries:

- `CONSTITUTION.md`: what may never change (toolkit, UI Automation properties, pixel geometry at
  the recorded DPI, texts, synthetic data, nothing out of scope);
- `ARCHITECTURE.md`: toolkit and runtime, one class per window, how windows open each other,
  the data access and the seed;
- `BUILD-PLAN.md`: the order to build in, each step with the check that says it is done;
- `WINDOWS/<window>.md`: one document per window: picture, frame, every control with its
  locator, bounds, font, colours and rules, its texts, how it opens and closes, the validation
  seen and the recorded steps that happen in it.

Plain Python.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from labctl.desktop import locator

DIR = "spec"


def _esc(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|")


def _window_doc(app: str, wid: str, look: dict, nav: dict, rules: dict, steps: list[dict],
                model_screen) -> str:  # fmt: skip
    w = look["windows"].get(wid) or {}
    key = w.get("window")
    navw = next((x for x in nav["windows"] if x["window"] == key), {}) if nav else {}
    rw = next((x for x in rules["windows"] if x["window"] == key), {}) if rules else {}
    rules_by = {c["selector"]: c for c in rw.get("controls") or []}
    f = w.get("frame") or {}
    out = [f"# Window `{wid}`: {', '.join(w.get('titles') or [])}\n",
           f"- Kind: {w.get('kind')}; locator `{key}`",
           f"- Picture (data painted over): `{app}/look/{w['picture']}`" if w.get("picture")
           else "- Picture: none kept",
           f"- Frame: outer {f.get('size')}, client area {f.get('client')} (x, y in the window, "
           f"width, height), DPI {f.get('dpi')} (scale {f.get('scale')}), "
           f"{'resizable' if f.get('resizable') else 'fixed border'}"
           f"{', dialog frame' if f.get('dialog_frame') else ''}, minimise box "
           f"{'yes' if f.get('minimize_box') else 'no'}, maximise box "
           f"{'yes' if f.get('maximize_box') else 'no'}, opens at the {f.get('placed')}",
           f"- Client background: {w.get('client_background') or 'not sampled'}"]  # fmt: skip
    if w.get("sizes_note"):
        out.append(f"- {w['sizes_note']}")
    if navw:

        def refs(items):
            return "; ".join(f"step {r['step']} {r['action']} `{r['control']}`"
                             + (f" ({r.get('key') or r.get('menu')})" if r.get("key") or r.get("menu") else "")
                             for r in items) or "-"  # fmt: skip

        out.append(f"- Opened by: {'the application starts with it' if navw.get('first') and not navw['opened_by'] else refs(navw['opened_by'])}")  # fmt: skip
        out.append(f"- Closed by: {refs(navw['closed_by'])}")
        out.append(f"- Modal: {navw.get('modal')}")
        if navw.get("tabs"):
            out.append(f"- Tabs: {', '.join(navw['tabs'])}")
    menus = [m for m in (nav or {}).get("menus") or [] if m["window"] == key]
    if menus:
        out.append("\n## Menus\n")
        for m in menus:
            items = ", ".join(i["name"] + (f" ({i['shortcut']})" if i.get("shortcut") else "")
                              for i in m.get("items") or []) or "never opened: items not known"  # fmt: skip
            out.append(f"- **{m['menu']}**: {items}")
    out.append("\n## Controls\n")
    out.append("Bounds are x, y, width, height in the client area, physical pixels at the "
               "recorded DPI.\n")  # fmt: skip
    out.append(
        "| Locator | Type | AutomationId | Name | ClassName | Bounds | Font | Colours | Rules |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|")
    classes = {}
    if model_screen is not None:
        classes = {e.selector: (e.uia or {}).get("class_name") for e in model_screen.elements}
    for c in w.get("controls") or []:
        font = c.get("font")
        ftxt = f"{font['face']} {font.get('pt')}pt ({font.get('px')}px)" + (
            " bold" if (font.get("weight") or 400) >= 700 else "") if font else "toolkit default"  # fmt: skip
        col = c.get("colours") or {}
        ctxt = " / ".join(x for x in (col.get("background"), col.get("text")) if x)
        r = rules_by.get(c["selector"]) or {}
        from labctl.desktop import fieldrules

        flags = fieldrules._flags(r) if r else ""  # noqa: SLF001
        if c.get("dock_seen"):
            flags = (flags + "; " if flags else "") + f"docked {c['dock_seen']} (as seen)"
        if c.get("anchor_seen"):
            flags = (flags + "; " if flags else "") + f"anchored {c['anchor_seen']}"
        out.append(f"| `{_esc(c['selector'])}` | {c.get('type')} | {_esc(c.get('automation_id'))} | "
                   f"{_esc(c.get('name'))} | `{_esc(classes.get(c['selector']) or '')}` | "
                   f"{c.get('bounds')} | {ftxt} | {ctxt} | {_esc(flags)} |")  # fmt: skip
    for c in w.get("controls") or []:
        if c.get("columns"):
            cols = ", ".join(
                f"{x['header']} (x {x['x']}, width {x['width']})" for x in c["columns"]
            )
            out.append(f"\nGrid `{c['selector']}`: columns {cols}; header height "
                       f"{c.get('header_height')}, row height {c.get('row_height')}, row header "
                       f"width {c.get('row_header_width')} (0: no row headers).")  # fmt: skip
    texts = (look.get("texts") or {}).get(wid, {}).get("texts") or []
    if texts:
        out.append("\n## Texts (use verbatim)\n")
        for t in texts:
            if t.get("tooltip") and not t.get("text"):
                out.append(f'- tooltip: "{t["tooltip"]}"')
                continue
            extra = []
            if t.get("written"):
                extra.append(f"written `{t['written']}`")
            if t.get("shortcut"):
                extra.append(f"shortcut {t['shortcut']}")
            if t.get("menu"):
                extra.append(f"in menu {t['menu']}")
            if t.get("tooltip"):
                extra.append(f'tooltip "{t["tooltip"]}"')
            out.append(
                f'- {t["type"]}: "{t["text"]}"' + (f" ({'; '.join(extra)})" if extra else "")
            )
    if rw.get("validation"):
        out.append("\n## Validation seen\n")
        for v in rw["validation"]:
            o = v["outcome"]
            what = f'message box "{o["message_box"]}": {o["text"]}' if isinstance(o, dict) else o
            fields = ", ".join(
                f"{locator.parse(k).get('AutomationId') or k}="
                + (x["item"] if "item" in x else f"{x.get('shape')} ({x.get('length')} chars)"
                   if "shape" in x else x.get("toggle", ""))
                for k, x in v["fields"].items())  # fmt: skip
            out.append(
                f"- step {v['step']}: {v['action']} `{v['action_on']}` with {fields} → {what}"
            )
    mine = [s for s in steps if s.get("window") == key]
    if mine:
        out.append("\n## Recorded steps in this window\n")
        for s in mine:
            bits = [f"{s['n']}. {s['action']}"]
            if s.get("selector"):
                bits.append(f"`{s['selector']}`")
            for k in ("typed_shape", "item", "key", "menu_path", "state"):
                if s.get(k):
                    bits.append(f"{k.replace('_', ' ')} `{s[k]}`")
            if s.get("password"):
                bits.append("(password)")
            if s.get("rows_after"):
                bits.append("rows after: " + ", ".join(f"{v}" for v in s["rows_after"].values()))
            if s.get("picture"):
                bits.append(f"picture `steps/{s['picture']}`")
            out.append("- " + " ".join(bits))
    return "\n".join(out) + "\n"


def write(root: Path, app_id: str, model, look: dict | None, nav: dict | None, rules: dict | None,
          steps_docs: list[dict]) -> list[Path]:  # fmt: skip
    app = f"apps/{app_id}"
    out = Path(root) / "apps" / app_id / DIR
    shutil.rmtree(out, ignore_errors=True)
    if not look:
        return []
    (out / "WINDOWS").mkdir(parents=True, exist_ok=True)
    d = model.stack.desktop or {}
    tk = d.get("toolkit") or "the recorded toolkit"
    rt = d.get("runtime") or {}
    runtime = (
        " ".join(x for x in (rt.get("name"), rt.get("version")) if x)
        if isinstance(rt, dict)
        else str(rt)
    )
    steps = [s for doc in steps_docs for s in doc["steps"]]
    screens = {(s.desktop or {}).get("window", {}).get("selector"): s for s in model.screens}
    written = []
    order = list(look["windows"])
    for wid in order:
        key = look["windows"][wid]["window"]
        path = out / "WINDOWS" / f"{wid}.md"
        path.write_text(_window_doc(app, wid, look, nav or {}, rules or {}, steps, screens.get(key)),
                        encoding="utf-8")  # fmt: skip
        written.append(path)
    sysf = (look.get("theme") or {}).get("system") or {}
    (out / "CONSTITUTION.md").write_text(f"""# Constitution: `{app_id}` (desktop)

These hold for every line of the demo app. A conflict with anything else is decided by them.

1. **Same toolkit and runtime**: {tk}{f' on {runtime}' if runtime else ''}. Bots find controls by the UI
   Automation properties the toolkit generates; another toolkit gives other selectors.
2. **Same UI Automation properties**: every AutomationId, Name, ControlType and ClassName in
   `{app}/spec/WINDOWS/*.md` and the brief, in the same parent. Never rename or merge controls.
3. **Same geometry**: each window's client area and each control's bounds as listed, in physical
   pixels at {sysf.get('dpi', 'the recorded')} DPI. Fonts as listed, else the toolkit default.
4. **Same words**: titles, labels, buttons, menus, column headers and message boxes exactly as in
   `{app}/look/texts.json`, with the same access keys (`&`).
5. **Same behaviour seen**: the windows open and close after the same actions
   (`{app}/navigation.json`), and the validation seen happens (`{app}/rules.json`).
6. **Synthetic data only**: load exactly `{app}/db/seed.sql`; the replay types the values in
   `{app}/steps/*.values.yaml`. No real data anywhere.
7. **Nothing out of scope** and nothing invented: what was not observed keeps the toolkit's
   default and is listed in BUILD-NOTES.md as an assumption.
""", encoding="utf-8")  # fmt: skip
    nav_lines = []
    for w in (nav or {}).get("windows") or []:
        how = "starts the application" if w.get("first") and not w["opened_by"] else ", ".join(
            f"step {r['step']} {r['action']} `{r['control']}`" for r in w["opened_by"]) or "-"  # fmt: skip
        nav_lines.append(f"- `{w['id']}` ({w['kind']}): opened by {how}; modal: {w.get('modal')}")
    tables = ", ".join(f"`{t.name}`" for t in model.tables) or "none"
    (out / "ARCHITECTURE.md").write_text(f"""# Architecture: `{app_id}` (desktop)

## Stack

- Toolkit: {tk}; runtime: {runtime or 'as the brief says'}; one executable.
- Database: SQLite file (path from `DATABASE_URL`, `sqlite:///C:/path/{app_id}.db`, default a file
  next to the executable), tables {tables} from `{app}/db/schema.sql`, rows from `{app}/db/seed.sql`.
- No network service was seen: the real application's data access is not known (the recorder sees
  screens, not SQL). Keep the data access plain.

## Windows

One class per window, named after its AutomationId where it has one. How they open:

{chr(10).join(nav_lines)}

## Look

Geometry, fonts and colours per window: `{app}/look/windows.json` and `{app}/spec/WINDOWS/*.md`;
the pictures to match: `{app}/look/screens/`. The machine recorded at {sysf.get('dpi', '?')} DPI,
text scale {sysf.get('text_scale_percent', 100)} %.
""", encoding="utf-8")  # fmt: skip
    plan = [f"# Build plan: `{app_id}` (desktop)\n",
            "Each step ends with its check. Do them in order.\n",
            f"1. Project skeleton in `{app}/src/` for {tk}; `build.ps1` that publishes it. "
            "Check: it builds offline.",
            f"2. Database: create the tables of `{app}/db/schema.sql` when absent, load "
            f"`{app}/db/seed.sql` when the tables are empty. Check: the rows are there."]  # fmt: skip
    for i, wid in enumerate(order, start=3):
        plan.append(f"{i}. Window `{wid}` from `{app}/spec/WINDOWS/{wid}.md`: controls with their "
                    "properties and bounds, texts, rules, the windows it opens. Check: it looks "
                    f"like `{app}/look/screens/{wid}.png`.")  # fmt: skip
    n = len(order) + 3
    plan.append(f"{n}. Replay: `labctl desktop verify {app_id} --exe <your exe>` with the "
                f"`verify_options` of `{app}/steps/*.values.yaml`, when a recording is available "
                "to you. Check: every step passes.")  # fmt: skip
    plan.append(
        f"{n + 1}. BUILD-NOTES.md: what you built, the checks and their results, every assumption."
    )
    (out / "BUILD-PLAN.md").write_text("\n".join(plan) + "\n", encoding="utf-8")
    return written + [out / "CONSTITUTION.md", out / "ARCHITECTURE.md", out / "BUILD-PLAN.md"]
