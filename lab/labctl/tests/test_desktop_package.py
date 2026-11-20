"""The desktop build package beyond the brief: pictures without data, the look, the navigation
map, field rules, recorded steps with seed and replay values, the build documents, and the
judge's comparisons. Runs everywhere, from a value-free recording of the reference app CaseDesk
(WinForms) made with window frames, fonts and pictures (`fixtures/desktop/casedesk-look-rec`)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from labctl import factory
from labctl.builder import package
from labctl.desktop import pictures, privacy, verify

FIX = Path(__file__).parent / "fixtures" / "desktop" / "casedesk-look-rec"
REPO = Path(__file__).resolve().parents[3]
DATA = ("C-1001", "Ada Example", "Ben Sample", "tester", "Card resent", "Card not received")


# --- pictures ---------------------------------------------------------------------------------


def test_png_round_trip_and_upscale() -> None:
    rgb = bytes([10, 20, 30, 200, 100, 0] * 3)  # 3 x 2 pixels
    png = pictures.write_png(3, 2, rgb)
    assert pictures.read_png(png) == (3, 2, rgb)
    w, h, big = pictures.read_png(pictures.upscale(png, 2))
    assert (w, h) == (6, 4) and big[:6] == bytes([10, 20, 30, 10, 20, 30])


def test_places_cover_values_data_names_and_a_masked_title() -> None:
    raw = {"type": "Window", "name": "Case C-1001", "hwnd": 7, "rect": [0, 0, 300, 200],
           "children": [
               {"type": "TitleBar", "rect": [30, 0, 260, 40], "children": [
                   {"type": "Button", "name": "Close", "rect": [250, 0, 40, 40]}]},
               {"type": "Text", "aid": "lblCustomer", "name": "Customer", "rect": [5, 50, 80, 20]},
               {"type": "Edit", "aid": "txtCustomer", "name": "Customer", "value": "Ada Example",
                "rect": [90, 50, 150, 24], "hwnd": 9},
               {"type": "Edit", "aid": "txtPassword", "password": True, "rect": [90, 80, 150, 24]},
               {"type": "StatusBar", "rect": [0, 170, 300, 30], "children": [
                   {"type": "Text", "name": "3 cases", "rect": [2, 172, 60, 24]}]}]}  # fmt: skip
    clean = privacy.clean(raw, set())
    found = pictures.places(raw, clean)
    by_rect = {tuple(p["rect"]): p for p in found}
    assert by_rect[(90, 50, 150, 24)]["text"] == "Xxx Xxxxxxx"
    assert by_rect[(90, 50, 150, 24)]["hwnd"] == 9
    assert by_rect[(90, 80, 150, 24)]["text"] == ""  # a password: painted over, nothing drawn
    assert by_rect[(2, 172, 60, 24)]["text"] == "9 xxxxx"
    title = next(p for p in found if p.get("caption"))
    assert title["text"] == "Case X-9999" and title["rect"] == [30, 0, 220, 40]
    assert (5, 50, 80, 20) not in by_rect  # a label is structure: never painted over
    assert pictures.secrets_of(found) >= {"adaexample", "3cases", "casec1001"}
    kept = pictures.kept_texts(clean)
    assert "customer" in kept and "adaexample" not in kept


def test_ocr_leak_rule() -> None:
    secrets = {"adaexample", "c1001", "open", "ab"}
    kept = {"open"}  # also a button caption: it shows anyway
    assert pictures.leaks("Case C-1001\nCustomer Ada Example", secrets, kept) == 2
    assert pictures.leaks("Open  X-9999  Xxx Xxxxxxx", secrets, kept) == 0


def test_a_caption_drawn_inside_a_kept_control_is_kept() -> None:
    raw = {"type": "Window", "name": "Cases", "children": [
        {"type": "Button", "aid": "btnSearch", "name": "Search", "children": [
            {"type": "Text", "name": "_Search"}]},
        {"type": "DataGrid", "aid": "dgv", "children": [
            {"type": "DataItem", "name": "Ada Example", "children": [
                {"type": "Text", "name": "Ada Example"}]}]}]}  # fmt: skip
    clean = privacy.clean(raw, set())
    btn, grid = clean["children"]
    assert btn["children"][0]["name"] == "_Search" and btn["children"][0]["name_is"] == "structure"
    assert grid["children"][0]["children"][0]["name_is"] == "data"


def test_a_combo_box_drop_button_is_structure() -> None:
    raw = {"type": "Window", "name": "Case", "children": [
        {"type": "Text", "aid": "lblStatus", "name": "Status"},
        {"type": "ComboBox", "aid": "cboStatus", "name": "Status", "value": "Waiting on vendor",
         "children": [{"type": "Text", "name": "Waiting on vendor"},
                      {"type": "Button", "name": "Open"}]}]}  # fmt: skip
    combo = privacy.clean(raw, set())["children"][1]
    text, button = combo["children"]
    assert button["name"] == "Open" and button["name_is"] == "structure"
    assert text["name_is"] == "data"


@pytest.mark.skipif(sys.platform != "win32", reason="draws the system icons with GDI")
def test_the_icon_of_a_message_box_is_recognised() -> None:
    from labctl.desktop import winlook

    size, w, h = 48, 120, 100
    for name in winlook.ICONS:
        icon = winlook._icon_pixels(name, size, 0xFFFFFF)  # noqa: SLF001
        px = bytearray(b"\xff\xff\xff\xff" * w * h)
        for y in range(size):
            row = ((20 + y) * w + 30) * 4
            px[row : row + size * 4] = icon[y * size * 4 : (y + 1) * size * 4]
        assert winlook.message_icon((w, h, bytes(px)), [30, 20, size, size]) == name
    blank = (w, h, b"\xff\xff\xff\xff" * w * h)
    assert winlook.message_icon(blank, [30, 20, size, size]) is None


# --- the package ------------------------------------------------------------------------------


def _lab(tmp_path: Path) -> Path:
    root = tmp_path / "lab"
    (root / "docs").mkdir(parents=True)
    shutil.copy(REPO / "AGENTS.md", root / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", root / "docs" / "factory-playbook.md")
    (root / "flows").mkdir()
    flow = {"id": "casedesk-update", "app": "casedesk", "requested_by": "test",
            "purpose": "bot updates a case", "steps": [
                {"screen": "Sign in", "do": "sign in"}, {"screen": "CaseDesk - Cases", "do": "search"},
                {"screen": "Case X-9999", "do": "edit and save"}]}  # fmt: skip
    (root / "flows" / "casedesk-update.yaml").write_text(yaml.safe_dump(flow), encoding="utf-8")
    shutil.copytree(FIX, root / "evidence" / "casedesk" / "flow-1")
    return root


def test_brief_writes_the_desktop_package(tmp_path: Path) -> None:
    root = _lab(tmp_path)
    factory.generate(root, "casedesk", "CaseDesk")
    app = root / "apps" / "casedesk"
    brief = (app / "BRIEF.md").read_text(encoding="utf-8")
    for section in ("## Same look", "## Windows and how they open",
                    "## Field rules and validation seen",
                    "## Recorded steps, seed data and replay values", "## Build documents"):  # fmt: skip
        assert section in brief, section
    # Look: frames, bounds, fonts, colours; pictures without data.
    windows = json.loads((app / "look" / "windows.json").read_text(encoding="utf-8"))
    main = windows["MainForm"]
    assert main["frame"]["client"][2:] == [590, 330] and main["frame"]["dpi"] == 144
    ctl = {c["selector"]: c for c in main["controls"]}
    search = ctl["uia:AutomationId=txtSearch;ControlType=Edit"]
    assert search["bounds"] == [90, 36, 220, 31] and search["font"]["face"] == "Segoe UI"
    assert ctl["uia:AutomationId=statusMain;ControlType=StatusBar"]["dock_seen"] == "bottom"
    grid = ctl["uia:AutomationId=dgvCases;ControlType=DataGrid"]
    assert [c["header"] for c in grid["columns"]] == ["Case No", "Customer", "Status", "Opened"]
    assert windows["CaseDetailForm"]["frame"]["placed"] == "centre of its owner"
    assert {"MainForm.png", "LoginForm.png", "msgbox-sign-in.png"} <= {
        p.name for p in (app / "look" / "screens").glob("*.png")}  # fmt: skip
    texts = json.loads((app / "look" / "texts.json").read_text(encoding="utf-8"))
    menu = {t["text"]: t for t in texts["MainForm"]["texts"] if t["type"] == "MenuItem"}
    assert menu["Exit"]["written"] == "E&xit" and menu["Refresh"]["shortcut"] == "F5"
    # Navigation: who opens what.
    nav = json.loads((app / "navigation.json").read_text(encoding="utf-8"))
    wins = {w["id"]: w for w in nav["windows"]}
    assert wins["LoginForm"]["first"] is True
    assert wins["CaseDetailForm"]["opened_by"][0]["control"] == (
        "uia:AutomationId=btnOpen;ControlType=Button")  # fmt: skip
    assert wins["msgbox-case"]["modal"] is True
    assert any(m["menu"] == "Help" and m["items"] is None for m in nav["menus"])
    # Rules: what Win32 says and the validation seen, with typed shapes.
    rules = json.loads((app / "rules.json").read_text(encoding="utf-8"))
    detail = next(w for w in rules["windows"] if w["id"] == "CaseDetailForm")
    by = {c["selector"]: c for c in detail["controls"]}
    assert by["uia:AutomationId=txtSummary;ControlType=Edit"]["multiline"] is True
    assert by["uia:AutomationId=cboStatus;ControlType=ComboBox"]["combo_style"] == "dropdown-list"
    assert by["uia:AutomationId=txtCaseNo;ControlType=Edit"]["on_open"]["read_only"] is True
    refused = next(v for v in detail["validation"] if isinstance(v["outcome"], dict))
    summary = refused["fields"]["uia:AutomationId=txtSummary;ControlType=Edit"]
    assert summary["length"] == 4 and refused["fields"][
        "uia:AutomationId=cboStatus;ControlType=ComboBox"]["item"] == "Resolved"  # fmt: skip
    # Seed and replay values made together.
    values = yaml.safe_load((app / "steps" / "flow-1.values.yaml").read_text(encoding="utf-8"))
    search_value = next(s["value"] for s in values["steps"] if s["automation_id"] == "txtSearch")
    seed = json.loads((app / "db" / "seed.json").read_text(encoding="utf-8"))
    ids = [r["case_no"] for r in seed["cases"]]
    assert len(ids) == 4 and sum(search_value in i for i in ids) == 3
    assert [r["status"] for r in seed["cases"]] == ["Open", "In progress", "Open", "Closed"]
    pw = [s for s in values["steps"] if s.get("password")]
    assert [s["value"] for s in pw] == ["Wrong-Pass-1", "Demo-Pass-1"]
    assert seed["app_users"][0]["password"] == "Demo-Pass-1"
    steps = json.loads((app / "steps" / "flow-1.json").read_text(encoding="utf-8"))["steps"]
    assert any(s.get("rows_after") == {"uia:AutomationId=dgvCases;ControlType=DataGrid": 3}
               for s in steps)  # fmt: skip
    # Documents.
    for doc in ("CONSTITUTION.md", "ARCHITECTURE.md", "BUILD-PLAN.md", "WINDOWS/MainForm.md"):
        assert (app / "spec" / doc).is_file(), doc
    # Nothing written holds data.
    for p in app.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".md", ".yaml", ".sql"):
            text = p.read_text(encoding="utf-8")
            assert not any(d in text for d in DATA), p
    # The package carries it all and the task points at it.
    zipped = package.build(root, "casedesk", tmp_path / "out")
    names = {f["path"] for f in package.read_manifest(zipped)["files"]}
    for rel in ("look/windows.json", "look/screens/MainForm.png", "navigation.json",
                "rules.json", "steps/flow-1.json", "steps/flow-1.values.yaml",
                "steps/flow-1/0011.png", "db/seed.sql", "spec/BUILD-PLAN.md"):  # fmt: skip
        assert f"apps/casedesk/{rel}" in names, rel
    task = package.prompt(root, "casedesk", None)
    assert "look/" in task and "seed.sql" in task and "values.yaml" in task


def test_focus_icon_and_grid_behaviour_reach_the_package(tmp_path: Path) -> None:
    root = _lab(tmp_path)
    rec = root / "evidence" / "casedesk" / "flow-1"
    # What a recorder that reads the keyboard focus and the message box icon writes.
    for name, mark in (("0001.json", "txtUser"), ("0005.json", None), ("0026.json", None)):
        path = rec / "trees" / name
        tree = json.loads(path.read_text(encoding="utf-8"))
        for node in tree.get("children") or []:
            if mark and node.get("aid") == mark:
                node["focused"] = True
            if node.get("type") == "Image":
                node["win32"] = {"icon": "warning"}
        path.write_text(json.dumps(tree), encoding="utf-8")
    factory.generate(root, "casedesk", "CaseDesk")
    app = root / "apps" / "casedesk"
    rules = json.loads((app / "rules.json").read_text(encoding="utf-8"))
    wins = {w["id"]: w for w in rules["windows"]}
    assert wins["LoginForm"]["focus_on_open"] == "uia:AutomationId=txtUser;ControlType=Edit"
    assert wins["MainForm"]["focus_on_open"].startswith("not recorded")
    grid = next(c for c in wins["MainForm"]["controls"] if c.get("grid"))["grid"]
    assert grid["cells_read_only"] is True and grid["headers_clickable"] is True
    assert grid["sorting"].startswith("not observed")
    refused = next(v for v in wins["CaseDetailForm"]["validation"]
                   if isinstance(v["outcome"], dict))  # fmt: skip
    assert refused["outcome"]["icon"] == "warning"
    assert any("search" in u for u in rules["unknown"])
    nav = json.loads((app / "navigation.json").read_text(encoding="utf-8"))
    box = next(w for w in nav["windows"] if w["id"] == "msgbox-case")
    assert box["icon"] == "warning" and box["buttons"] == ["OK (default)"]
    brief = (app / "BRIEF.md").read_text(encoding="utf-8")
    assert "keyboard focus when it opens: `uia:AutomationId=txtUser;ControlType=Edit`" in brief
    assert "(warning icon)" in brief and "- Not known: how a search matches" in brief
    zipped = package.build(root, "casedesk", tmp_path / "out")
    purposes = {f["path"]: f["purpose"] for f in package.read_manifest(zipped)["files"]}
    assert purposes["apps/casedesk/spec/WINDOWS/MainForm.md"].startswith("Window file")
    assert "window" in purposes["apps/casedesk/look/windows.json"].lower()
    assert "page" not in purposes["apps/casedesk/steps/flow-1.json"]


def test_package_refuses_a_picture_the_recorder_did_not_keep(tmp_path: Path) -> None:
    root = _lab(tmp_path)
    factory.generate(root, "casedesk", "CaseDesk")
    pic = root / "apps" / "casedesk" / "steps" / "flow-1" / "0011.png"
    pic.write_bytes(pictures.write_png(1, 1, b"\x00\x00\x00"))
    try:
        package.build(root, "casedesk", tmp_path / "out")
    except package.PackageError as exc:
        assert "0011.png" in str(exc)
    else:
        raise AssertionError("a picture the recorder did not keep was packaged")


# --- the judge's comparisons ------------------------------------------------------------------


def test_geometry_rows_and_picture_comparison() -> None:
    tree = json.loads((FIX / "trees" / "0014.json").read_text(encoding="utf-8"))
    assert verify.compare_geometry(tree, tree) == []
    assert verify.compare_rows(tree, tree) == []
    moved = json.loads(json.dumps(tree))
    for c in moved["children"]:
        if c.get("aid") == "btnOpen":
            c["rect"][0] += 5
        if c.get("aid") == "dgvCases":
            c["children"] = c["children"][:2]
    diffs = {(d.check, d.element) for d in verify.compare_geometry(tree, moved)}
    assert ("geometry", "uia:AutomationId=btnOpen;ControlType=Button") in diffs
    rows = verify.compare_rows(tree, moved)
    assert rows and rows[0].check == "rows" and rows[0].actual == 1
    png = (FIX / "pictures" / "0014.png").read_bytes()
    assert verify.picture_difference(png, png) == 0.0
    w, h, rgb = pictures.read_png(png)
    other = pictures.write_png(w, h, bytes(255 - b for b in rgb))
    assert verify.picture_difference(png, other) > 50


def test_a_late_tree_is_not_used_for_rows_pictures_or_the_look(tmp_path: Path) -> None:
    """A step whose window tree was read only after later steps (`tree_late`, set by the
    recorder) gives no rows, no picture and no window picture; its order and typed shape stay."""
    root = _lab(tmp_path)
    rec = root / "evidence" / "casedesk" / "flow-1"
    lines = (rec / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    steps = [json.loads(line) for line in lines]
    for s in steps:
        if s["seq"] in (11, 14):  # the main window as it opens, and after Enter
            s["tree_late"] = True
    (rec / "steps.jsonl").write_text("\n".join(json.dumps(s) for s in steps) + "\n",
                                     encoding="utf-8")  # fmt: skip
    factory.generate(root, "casedesk", "CaseDesk")
    app = root / "apps" / "casedesk"
    doc = json.loads((app / "steps" / "flow-1.json").read_text(encoding="utf-8"))
    by_n = {s["n"]: s for s in doc["steps"]}
    assert by_n[14]["tree_late"] is True and "rows_after" not in by_n[14]
    assert "picture" not in by_n[14] and by_n[14]["action"] == "key"
    assert not (app / "steps" / "flow-1" / "0014.png").exists()
    windows = json.loads((app / "look" / "windows.json").read_text(encoding="utf-8"))
    assert windows["MainForm"]["from"] != "flow-1 step 11"
