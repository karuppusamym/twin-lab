"""The look of a desktop application, from its recordings: what `apps/<app>/look/` carries.

- `windows.json`: per window, its frame (size, client area, DPI, border, boxes, where it opens)
  and every control's place and size in the client area (physical pixels at the recorded DPI),
  its font (what Win32 reports, else "toolkit default"), colours sampled from the window's
  picture, and docking or anchoring where the recording shows it.
- `theme.json`: the machine's settings that change every window (DPI, system fonts, light or
  dark apps, high contrast) and the colours that repeat (window background, text).
- `texts.json`: the interface's own words per window: title (data masked), labels, buttons,
  check boxes, menus and items with their access keys and shortcuts, column headers, group and
  tab titles, tooltips, message boxes. Data is never there (only what the clean trees keep).
- `screens/<window>.png`: one picture per window and per menu opened, without data
  (labctl.desktop.pictures: painted over and checked with OCR when recorded).
- `manifest.json`: which recording step each picture comes from and how it was made.
- Fonts are named, never carried: the ones Windows and Java ship are not redistributable, and
  they are already on the machine the demo app runs on.

Plain Python: reads recordings, writes files.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path

from labctl.desktop import locator, pictures
from labctl.desktop import model as dmodel

DIR = "look"
WINDOW_FONTS = {
    "segoe ui", "segoe ui variable", "tahoma", "microsoft sans serif", "ms sans serif",
    "ms shell dlg", "ms shell dlg 2", "arial", "calibri", "consolas", "courier new", "verdana",
    "times new roman", "segoe ui symbol", "segoe mdl2 assets", "segoe fluent icons",
}  # fmt: skip
JAVA_FONTS = {"dialog", "dialoginput", "sansserif", "serif", "monospaced"}
TEXT_TYPES = {"Text", "Button", "CheckBox", "RadioButton", "MenuItem", "TabItem", "Header",
              "HeaderItem", "Group", "SplitButton", "Hyperlink"}  # fmt: skip


def slug(window: dict, kind: str) -> str:
    """A file name for a window: its AutomationId, else its title (`msgbox-sign-in`)."""
    sel = window.get("selector") or ""
    p = locator.parse(sel)
    base = p.get("AutomationId") if locator.stable_id(p.get("AutomationId")) else None
    base = base or p.get("Name") or window.get("title") or "window"
    s = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-") or "window"
    return f"msgbox-{s.lower()}" if kind == "message box" else s


def recordings(root: Path, app_id: str) -> list[dmodel.Recording]:
    ev = Path(root) / "evidence" / app_id
    return [dmodel.Recording(p) for p in sorted(ev.glob("*")) if dmodel.is_recording(p)]


def _kind(step: dict, tree: dict | None) -> str:
    props = (step.get("window") or {}).get("props") or {}
    if (tree or {}).get("message_box") or (
        props.get("ClassName") == "#32770" and not props.get("AutomationId")
    ):
        return "message box"
    return "window"


def _client(tree: dict) -> tuple[int, int, int, int]:
    f = tree.get("frame") or {}
    c = f.get("client")
    return tuple(c) if c and len(c) == 4 else (0, 0, *(tree.get("rect") or [0, 0, 0, 0])[2:])


def _png_colours(png: bytes | None):
    if not png:
        return None
    try:
        return pictures.read_png(png)
    except (ValueError, OSError):
        return None


def _sample(img, rect) -> dict:
    """Background and text colour of a rectangle of a picture (`#rrggbb`), or {}."""
    if img is None or not rect:
        return {}
    w, h, rgb = img
    x0, y0 = max(rect[0], 0), max(rect[1], 0)
    x1, y1 = min(rect[0] + rect[2], w), min(rect[1] + rect[3], h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return {}

    def at(x, y):
        i = (y * w + x) * 3
        return rgb[i], rgb[i + 1], rgb[i + 2]

    edge = Counter(at(x, y) for x in range(x0 + 2, x1 - 2, 2) for y in (y0 + 2, y1 - 3))
    edge.update(at(x, y) for y in range(y0 + 2, y1 - 2, 2) for x in (x0 + 2, x1 - 3))
    if not edge:
        return {}
    bg = edge.most_common(1)[0][0]
    inner = Counter(at(x, y) for y in range(y0 + 2, y1 - 2) for x in range(x0 + 2, x1 - 2, 2))
    far = {
        c: n
        for c, n in inner.items()
        if sum(abs(a - b) for a, b in zip(c, bg, strict=False)) > 120 and n > 1
    }
    fg = (
        max(far, key=lambda c: (sum(abs(a - b) for a, b in zip(c, bg, strict=False)), far[c]))
        if far
        else None
    )
    out = {"background": "#%02x%02x%02x" % bg}
    if fg:
        out["text"] = "#%02x%02x%02x" % fg
    return out


def _mnemonic(text: str, access_key: str | None) -> str | None:
    """`E&xit` from `Exit` and access key `x` / `Alt+x`: how the toolkit writes it."""
    if not text or not access_key:
        return None
    letter = access_key.split("+")[-1].strip()
    if len(letter) != 1:
        return None
    i = text.lower().find(letter.lower())
    return None if i < 0 else text[:i] + "&" + text[i:]


def _font_name(face: str) -> dict:
    low = (face or "").lower()
    if low in WINDOW_FONTS:
        return {"font": face, "carried": False,
                "why": "a font Windows ships (Microsoft licence, not redistributable); it is on "
                       "every Windows machine the demo app runs on"}  # fmt: skip
    if low in JAVA_FONTS:
        return {"font": face, "carried": False,
                "why": "a Java logical font: the Java runtime maps it to an installed font"}  # fmt: skip
    return {"font": face, "carried": False,
            "why": "not identified as redistributable: install it on the demo machine or use the "
                   "nearest installed font, and say so in BUILD-NOTES.md"}  # fmt: skip


class _Window:
    def __init__(self, key: str, kind: str):
        self.key, self.kind = key, kind
        self.window: dict = {}
        self.titles: Counter = Counter()
        self.snaps: list[tuple[str, int, dict, str | None]] = []  # (rec, seq, tree, picture)
        self.menus: list[tuple[str, int, dict, str | None, str]] = []  # (+ menu name)


def collect(recs: list[dmodel.Recording]) -> dict[str, _Window]:
    wins: dict[str, _Window] = {}
    last_menu: dict[str, str] = {}
    for rec in recs:
        for s in rec.steps:
            w = s.get("window") or {}
            key = w.get("selector")
            if not key:
                continue
            tree = rec.tree(s.get("tree"))
            win = wins.get(key)
            if win is None:
                win = wins[key] = _Window(key, _kind(s, tree))
                win.window = w
            if w.get("title"):
                win.titles[w["title"]] += 1
            pic = s.get("picture")
            if pic and not (rec.path / pic).is_file():
                pic = None  # dropped by the data check
            if s.get("action") == "menu" and s.get("menu_path"):
                last_menu[key] = s["menu_path"]
            if s.get("action") == "menu_open":
                if tree is not None:
                    win.menus.append((rec.id, s["seq"], tree, pic, last_menu.get(key, "menu")))
                continue
            if tree is not None and s.get("action") != "window_close":
                win.snaps.append((rec.id, s["seq"], tree, pic))
    return wins


def _best(win: _Window, recs: dict) -> tuple[str, int, dict, str | None] | None:
    """The snapshot that shows the window as it opens, with a picture: the first one with a
    picture whose window is enabled (no dialog over it), else the first one."""
    with_pic = [s for s in win.snaps if s[3]]
    for s in with_pic:
        if s[2].get("enabled", True):
            return s
    return with_pic[0] if with_pic else (win.snaps[0] if win.snaps else None)


def _controls(tree: dict, img) -> list[dict]:
    cx, cy, cw, ch = _client(tree)
    out = []
    named = _named_after(tree)
    for node, ancestors, idx in dmodel._elements_of(tree):  # noqa: SLF001 - the model's walk
        loc = locator.build(tree, node, ancestors, idx)
        rect = node.get("rect")
        item: dict = {"selector": loc["selector"], "type": node.get("type")}
        if node.get("aid") and locator.stable_id(node["aid"]):
            item["automation_id"] = node["aid"]
        if node.get("name") and node.get("name_is") != "data":
            item["name"] = node["name"]
        if loc["selector"] in named:
            item["name_from_label"] = named[loc["selector"]]
            own = re.sub(r"^(txt|cbo|cmb)", "lbl", node.get("aid") or "")
            if own and own != named[loc["selector"]]:
                item["name_note"] = (
                    f"named after `{named[loc['selector']]}`, not `{own}`: UI Automation names an "
                    "input after the label just before it in tab order (WinForms: the label with "
                    "the next lower TabIndex; a control that is no tab stop is still ordered by "
                    "its TabIndex)")  # fmt: skip
        if rect and rect[2] > 0 and rect[0] > -50:
            item["bounds"] = [rect[0] - cx, rect[1] - cy, rect[2], rect[3]]
            dock = _dock(item["bounds"], cw, ch)
            if dock:
                item["dock_seen"] = dock
        w32 = node.get("win32") or {}
        if w32.get("font"):
            item["font"] = w32["font"]
        colours = _sample(img, rect)
        if colours:
            item["colours"] = colours
        if node.get("type") in ("DataGrid", "Table"):
            item.update(_grid_geometry(node, rect))
        for flag in ("enabled", "visible"):
            if node.get(flag) is False:
                item[flag] = False
        out.append(item)
    return out


def _grid_geometry(node: dict, rect) -> dict:
    """Column headers with their x and width (relative to the grid), header and row height,
    and whether rows have a header column (the first column starts after a gap)."""
    out: dict = {}
    if not rect:
        return out
    for child in node.get("children") or []:
        heads = [k for k in child.get("children") or []
                 if k.get("type") in ("Header", "HeaderItem") and k.get("rect")]  # fmt: skip
        if heads and "columns" not in out:
            out["columns"] = [{"header": h.get("name") if h.get("name_is") != "data" else None,
                               "x": h["rect"][0] - rect[0], "width": h["rect"][2]}
                              for h in heads]  # fmt: skip
            out["header_height"] = heads[0]["rect"][3]
            out["row_header_width"] = max(heads[0]["rect"][0] - rect[0] - 2, 0)
        elif child.get("type") == "Custom" and child.get("rect") and "row_height" not in out and (
                child.get("children")):  # fmt: skip
            out["row_height"] = child["rect"][3]
            out["first_row_y"] = child["rect"][1] - rect[1]
    if "first_row_y" in out:
        out["note"] = (
            "UI Automation bounds: `first_row_y` is where the first row starts below the grid's "
            "top edge (the grid's border included). Make the toolkit's property give that: in "
            "WinForms a DataGridView's ColumnHeadersHeight is first_row_y minus the border "
            "(1 px with BorderStyle FixedSingle), not the header item's height."
        )
    return out


def _dock(b: list[int], cw: int, ch: int) -> str | None:
    """A control spanning the client area's full width at its top or bottom edge, or all of
    it: what a docked control looks like (UI Automation does not say Dock or Anchor)."""
    if not cw or not ch:
        return None
    full_w = b[0] <= 1 and abs(b[0] + b[2] - cw) <= 1
    full_h = b[1] <= 1 and abs(b[1] + b[3] - ch) <= 1
    if full_w and full_h:
        return "fill"
    if full_w and b[1] <= 1:
        return "top"
    if full_w and abs(b[1] + b[3] - ch) <= 1:
        return "bottom"
    return None


def _anchors(win: _Window) -> dict[str, str]:
    """Selectors whose distance to the right or bottom edge stayed the same while the window
    changed size: anchored there. Only when the recording saw two sizes."""
    seen: dict[str, list[tuple[int, int, list[int]]]] = {}
    for _r, _s, tree, _p in win.snaps:
        cx, cy, cw, ch = _client(tree)
        for node, ancestors, idx in dmodel._elements_of(tree):  # noqa: SLF001
            rect = node.get("rect")
            if rect and rect[2] > 0:
                sel = locator.build(tree, node, ancestors, idx)["selector"]
                seen.setdefault(sel, []).append((cw, ch, [rect[0] - cx, rect[1] - cy, *rect[2:]]))
    out = {}
    for sel, obs in seen.items():
        sizes = {(o[0], o[1]) for o in obs}
        if len(sizes) < 2:
            continue
        a, b = obs[0], next(o for o in obs if (o[0], o[1]) != (obs[0][0], obs[0][1]))
        edges = ["top" if a[2][1] == b[2][1] else None, "left" if a[2][0] == b[2][0] else None]
        if a[0] - a[2][0] - a[2][2] == b[0] - b[2][0] - b[2][2]:
            edges.append("right")
        if a[1] - a[2][1] - a[2][3] == b[1] - b[2][1] - b[2][3]:
            edges.append("bottom")
        out[sel] = ", ".join(e for e in edges if e) or "none (moves and sizes in proportion)"
    return out


def _label_keys(tree: dict) -> dict[str, str]:
    """A label's access key, read from the input named after it (UI Automation gives the
    input, not the label, the access key: WinForms `&User name` labels `txtUser`)."""
    keys: dict[str, str] = {}
    for node, _anc, _i in locator.walk(tree):
        if node.get("type") in ("Edit", "ComboBox", "List", "Spinner") and node.get(
                "access_key") and node.get("name") and node.get("name_is") != "data":  # fmt: skip
            keys.setdefault(node["name"], node["access_key"])
    return keys


def _named_after(tree: dict) -> dict[str, str]:
    """selector -> the label an input takes its Name from, when that is not its own label
    (UI Automation names an input after the label before it in tab order)."""
    labels = {}
    for node, _anc, _idx in locator.walk(tree):
        if node.get("type") == "Text" and node.get("aid") and node.get("name_is") == "structure":
            labels.setdefault(node["name"], node["aid"])
    out = {}
    for node, anc, idx in locator.walk(tree):
        if node.get("type") in ("Edit", "ComboBox") and anc and node.get("name") in labels:
            out[locator.build(tree, node, anc, idx)["selector"]] = labels[node["name"]]
    return out


def _texts(tree: dict, kind: str) -> list[dict]:
    out, seen = [], set()
    label_keys = _label_keys(tree)
    for node, ancestors, idx in locator.walk(tree):
        if any(a.get("type") == "TitleBar" for a in ancestors) or node.get("type") == "TitleBar":
            continue
        name = node.get("name")
        if not name or node.get("name_is") not in ("structure", "message"):
            if node.get("help") and node.get("name_is") != "data":
                out.append({"type": node.get("type"), "tooltip": node["help"]})
            continue
        t = node.get("type")
        if t not in TEXT_TYPES and t not in ("Edit", "ComboBox", "Window") and node.get(
            "name_is") != "message":  # fmt: skip
            continue
        if t in ("Edit", "ComboBox"):
            continue  # named after its label: the label is listed
        sel = locator.build(tree, node, ancestors, idx)["selector"] if ancestors else None
        key = (sel, name)
        if key in seen:
            continue
        seen.add(key)
        item = {"type": t, "text": name, "selector": sel}
        key_ = node.get("access_key") or (label_keys.get(name) if t == "Text" else None)
        mn = _mnemonic(name, key_)
        if mn:
            item["written"] = mn
        if node.get("accel"):
            item["shortcut"] = node["accel"]
        if node.get("help"):
            item["tooltip"] = node["help"]
        if node.get("name_is") == "message":
            item["role"] = "message box text"
        out.append(item)
    return out


def build(recs: list[dmodel.Recording], titles: dict[str, str] | None = None) -> dict:
    """Everything `write` puts in files, as one dict (no files read but the recordings)."""
    by_id = {r.id: r for r in recs}
    wins = collect(recs)
    system = next((r.session.get("system") for r in recs if r.session.get("system")), None) or {}
    out_windows, texts, shots, fonts = {}, {}, [], Counter()
    backgrounds, text_colours = Counter(), Counter()
    for key, win in wins.items():
        name = slug(win.window, win.kind)
        best = _best(win, by_id)
        if best is None:
            continue
        rec_id, seq, tree, pic = best
        img = _png_colours((by_id[rec_id].path / pic).read_bytes()) if pic else None
        frame = dict(tree.get("frame") or {})
        cx, cy, cw, ch = _client(tree)
        controls = _controls(tree, img)
        anchors = _anchors(win)
        for c in controls:
            if c["selector"] in anchors:
                c["anchor_seen"] = anchors[c["selector"]]
            if c.get("font"):
                fonts[c["font"]["face"]] += 1
            if c.get("colours", {}).get("text"):
                text_colours[c["colours"]["text"]] += 1
        client_bg = None
        if img is not None and cw:
            client_bg = _sample(img, [cx, cy, cw, ch]).get("background")
            if client_bg:
                backgrounds[client_bg] += 1
        entry = {
            "window": key,
            "kind": win.kind,
            "titles": sorted(win.titles) or [(titles or {}).get(key) or ""],
            "frame": frame,
            "client_background": client_bg,
            "controls": controls,
            "picture": f"screens/{name}.png" if pic else None,
            "from": f"{rec_id} step {seq}",
        }
        if frame.get("scale") and frame["scale"] != 1:
            entry["sizes_note"] = (
                f"Sizes are physical pixels at {frame.get('dpi')} DPI (scale {frame['scale']}); "
                f"at 96 DPI divide by {frame['scale']}."
            )
        out_windows[name] = entry
        if pic:
            shots.append({"picture": f"screens/{name}.png", "window": key, "kind": win.kind,
                          "from": f"{rec_id} step {seq}", "_src": by_id[rec_id].path / pic})  # fmt: skip
        tx = _texts(tree, win.kind)
        for m in win.menus:
            for item in _texts(m[2], "menu"):
                if item not in tx:
                    item["menu"] = m[4]
                    tx.append(item)
        texts[name] = {"window": key, "title": entry["titles"][0] if entry["titles"] else "",
                       "texts": tx}  # fmt: skip
        done_menus = set()
        for rec_m, seq_m, _tree_m, pic_m, menu in win.menus:
            if not pic_m or menu in done_menus:
                continue
            done_menus.add(menu)
            mslug = re.sub(r"[^A-Za-z0-9]+", "-", menu).strip("-").lower() or "menu"
            shots.append({"picture": f"screens/{name}--menu-{mslug}.png", "window": key,
                          "kind": "menu", "menu": menu, "from": f"{rec_m} step {seq_m}",
                          "_src": by_id[rec_m].path / pic_m})  # fmt: skip
    face_notes = [_font_name(f) for f, _n in fonts.most_common()]
    theme = {
        "system": system,
        "fonts": face_notes,
        "window_background": backgrounds.most_common(1)[0][0] if backgrounds else None,
        "text": text_colours.most_common(1)[0][0] if text_colours else None,
        "note": "Colours are sampled from the pictures (the application's pixels); fonts are "
        "what Win32 reports for controls with a window of their own. A control without a font "
        "entry uses the toolkit's default (WinForms: the form's font, Segoe UI 9 pt unless the "
        "form sets another; WPF: the system message font; Swing: the look and feel's).",
    }
    return {"windows": out_windows, "texts": texts, "theme": theme, "screens": shots}


def write(root: Path, app_id: str, recs: list[dmodel.Recording] | None = None) -> dict | None:
    """Write apps/<app>/look/ for a desktop application; None when it has no recording."""
    recs = recordings(root, app_id) if recs is None else recs
    out = Path(root) / "apps" / app_id / DIR
    if not recs:
        return None
    shutil.rmtree(out, ignore_errors=True)
    (out / "screens").mkdir(parents=True, exist_ok=True)
    doc = build(recs)
    import hashlib

    for shot in doc["screens"]:
        shutil.copyfile(shot["_src"], out / shot["picture"])
        shot["sha256"] = hashlib.sha256((out / shot["picture"]).read_bytes()).hexdigest()
    manifest = {
        "format": "twinlab-desktop-look/1",
        "how": "pictures printed from each window while it was recorded, every place showing data "
        "painted over and its shape drawn there, then read with OCR: a picture showing a data "
        "value was dropped (labctl.desktop.pictures)",
        "screens": [{k: v for k, v in s.items() if not k.startswith("_")} for s in doc["screens"]],
    }
    for name, data in (("windows.json", doc["windows"]), ("texts.json", doc["texts"]),
                       ("theme.json", doc["theme"]), ("manifest.json", manifest)):  # fmt: skip
        (out / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return doc


def brief_section(doc: dict | None, app: str) -> str:
    if not doc:
        return ""
    lines = ["## Same look\n",
             f"`{app}/look/` holds the look of every window, read while it was recorded:\n",
             "- `look/windows.json`: each window's frame (outer size, client area, DPI and scale, "
             "border, minimise/maximise boxes, where it opens) and each control's bounds in the "
             "client area in physical pixels, its font and colours. **Reproduce these bounds "
             "exactly at the recorded DPI** (WinForms: set `AutoScaleMode` so the sizes come out "
             "as listed, for example `None` with these pixel values).",
             "- `look/screens/*.png`: a picture of each window and each opened menu, data painted "
             "over and its shape drawn in its place. Your windows must look like these.",
             "- `look/texts.json`: the interface's words per window, with `&` access keys as the "
             "toolkit writes them (`E&xit`), shortcuts and tooltips. Use them verbatim.",
             "- `look/theme.json`: the machine's DPI and system fonts, the colours that repeat. "
             "Fonts are named, not carried: they ship with Windows or Java.\n"]  # fmt: skip
    sysf = (doc.get("theme") or {}).get("system") or {}
    if sysf:
        lines.append(f"Recorded at {sysf.get('dpi')} DPI, text scale {sysf.get('text_scale_percent')} %"
                     f", {'light' if sysf.get('apps_light_theme', True) else 'dark'} app theme"
                     f"{', high contrast' if sysf.get('high_contrast') else ''}.\n")  # fmt: skip
    lines.append("| Window | Kind | Client area (px) | DPI | Border | Opens at | Picture |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, w in doc["windows"].items():
        f = w.get("frame") or {}
        c = f.get("client") or []
        border = ("resizable" if f.get("resizable") else "fixed") + (
            ", dialog frame" if f.get("dialog_frame") else "")  # fmt: skip
        boxes = [b for b, on in (("min", f.get("minimize_box")), ("max", f.get("maximize_box")))
                 if on]  # fmt: skip
        if boxes:
            border += f" ({'/'.join(boxes)} boxes)"
        lines.append(f"| `{name}` | {w['kind']} | {c[2] if len(c) == 4 else '?'} x "
                     f"{c[3] if len(c) == 4 else '?'} | {f.get('dpi', '?')} | {border} | "
                     f"{f.get('placed') or '?'} | {w.get('picture') or 'none'} |")  # fmt: skip
    lines.append("")
    return "\n".join(lines) + "\n"


def package_files(root: Path, app_id: str) -> dict[str, Path]:
    out = Path(root) / "apps" / app_id / DIR
    if not (out / "manifest.json").is_file():
        return {}
    return {f"apps/{app_id}/{DIR}/{p.relative_to(out).as_posix()}": p
            for p in sorted(out.rglob("*")) if p.is_file() and not p.is_symlink()}  # fmt: skip
