"""Desktop path, plain Python (runs on Linux CI): privacy, locators, detection, tree comparison."""

from __future__ import annotations

from labctl.desktop import detect, locator, privacy
from labctl.desktop.verify import compare_trees, value_for


def raw_login() -> dict:
    """A raw window tree as the recorder captures it (names and values as the app shows them)."""
    return {
        "type": "Window", "aid": "LoginForm", "name": "Sign in - Customer: Ada Example",
        "cls": "WindowsForms10.Window.8.app.0.1f550a4_r3_ad1", "fw": "WinForm", "enabled": True,
        "children": [
            {"type": "Text", "aid": "lblUser", "name": "User name"},
            {"type": "Edit", "aid": "txtUser", "name": "User name", "value": "ada.example77",
             "patterns": ["value"]},
            {"type": "Edit", "aid": "txtPassword", "name": "Password", "password": True},
            {"type": "Text", "aid": "", "name": "Ada Example"},
            {"type": "Button", "aid": "btnSignIn", "name": "Sign in", "access_key": "Alt+s"},
            {"type": "ComboBox", "aid": "cboStatus", "name": "Status", "value": "Open", "children": [
                {"type": "List", "children": [{"type": "ListItem", "name": n}
                                              for n in ("Open", "In progress", "Resolved")]}]},
            {"type": "ComboBox", "aid": "cboCustomer", "name": "Customer", "children": [
                {"type": "Edit", "name": "Customer", "value": "Ben"},
                {"type": "List", "children": [{"type": "ListItem", "name": "Ben Sample"}]}]},
            {"type": "DataGrid", "aid": "dgvCases", "rows": 1, "children": [
                {"type": "Custom", "name": "Top Row", "children": [
                    {"type": "Header", "name": "Case No"}, {"type": "Header", "name": "Opened"}]},
                {"type": "Custom", "name": "Row 0", "children": [
                    {"type": "DataItem", "name": "Case No Row 0", "value": "C-1001"},
                    {"type": "DataItem", "name": "Opened Row 0", "value": "2026-10-01"}]}]},
            {"type": "StatusBar", "children": [{"type": "Text", "aid": "lblCount", "name": "4 cases"}]},
        ],
    }  # fmt: skip


def test_privacy_keeps_structure_and_masks_data() -> None:
    known: set[str] = set()
    t = privacy.clean(raw_login(), known)
    by = {c.get("aid") or c.get("name"): c for c in t["children"]}
    assert t["name"] == "Sign in - Customer: Xxx Xxxxxxx"  # after ':' is data
    assert by["lblUser"]["name"] == "User name" and by["lblUser"]["name_is"] == "structure"
    assert by["txtUser"]["value"] == "xxx.xxxxxxx99" and "ada" not in str(t).lower()
    assert "value" not in by["txtPassword"] and by["txtPassword"]["password"] is True
    assert by["btnSignIn"]["name"] == "Sign in"
    loose = [c for c in t["children"] if c.get("type") == "Text" and not c.get("aid")][0]
    assert loose["name"] == "Xxx Xxxxxxx" and loose["name_is"] == "data"
    status = by["cboStatus"]
    assert status["items_are"] == "code-list"
    items = [i["name"] for i in status["children"][0]["children"]]
    assert items == ["Open", "In progress", "Resolved"]
    assert by["cboCustomer"]["items_are"] == "data"  # editable: a picker, never a code list
    assert "Ben Sample" not in str(by["cboCustomer"])
    grid = by["dgvCases"]
    headers = [h["name"] for h in grid["children"][0]["children"]]
    assert headers == ["Case No", "Opened"]
    cells = grid["children"][1]["children"]
    assert cells[0]["value"] == "X-9999" and cells[1]["kind"] == "date"
    status_bar = next(c for c in t["children"] if c["type"] == "StatusBar")
    assert "C-1001" not in str(t) and status_bar["children"][0]["name"] == "9 xxxxx"
    assert "example" in known  # data words seen, in memory, to mask them elsewhere


def test_privacy_title_and_message_box() -> None:
    assert privacy.mask_title("Case C-1001") == "Case X-9999"
    assert privacy.mask_title("CaseDesk - Cases") == "CaseDesk - Cases"
    assert privacy.mask_title("Ada Example - CaseDesk", {"ada"}) == "Xxx Example - CaseDesk"
    box = {"type": "Window", "name": "Case", "cls": "#32770", "children": [
        {"type": "Text", "aid": "65535", "name": "A resolved case needs a summary of 10 characters."},
        {"type": "Button", "aid": "2", "name": "OK"}]}  # fmt: skip
    t = privacy.clean(box)
    assert t["children"][0]["name_is"] == "message"
    assert t["children"][0]["name"].startswith("A resolved case")
    assert privacy.code_list(["Open", "Closed"]) and not privacy.code_list(["Room 101"])


def test_locator_syntax_and_escapes() -> None:
    sel = locator.format({"AutomationId": "a;b=c", "ControlType": "Edit"})
    assert sel == r"uia:AutomationId=a\;b\=c;ControlType=Edit"
    assert locator.parse(sel) == {"AutomationId": "a;b=c", "ControlType": "Edit"}
    assert locator.parse("uia:ControlType=Edit;Index=2")["Index"] == 2
    assert (
        locator.normalise_class("WindowsForms10.Edit.app.0.141b42a_r6_ad1")
        == "WindowsForms10.Edit.app.0.*"
    )
    assert locator.normalise_class("HwndWrapper[App;;9a2f]") == "HwndWrapper[App;;*]"
    assert not locator.stable_id("42339559") and locator.stable_id("txtCase")


def test_locator_build_unique_and_ambiguous() -> None:
    t = privacy.clean(raw_login())
    nodes = {n.get("aid"): (n, a, i) for n, a, i in locator.walk(t)}
    n, a, i = nodes["txtUser"]
    loc = locator.build(t, n, a, i)
    assert loc["selector"] == "uia:AutomationId=txtUser;ControlType=Edit" and loc["matches"] == 1
    assert loc["path"] == ["uia:AutomationId=LoginForm;ControlType=Window"]
    assert any(c["selector"].startswith("uia:Name=User name") for c in loc["candidates"])
    twin = {"type": "Window", "aid": "W", "children": [
        {"type": "Button", "name": "Go", "cls": "Button"}, {"type": "Button", "name": "Go", "cls": "Button"}]}  # fmt: skip
    t2 = privacy.clean(twin)
    n, a, i = list(locator.walk(t2))[1]
    loc = locator.build(t2, n, a, i)
    # No flat locator is unique; the Index form scoped by the path is.
    assert loc["selector"] == "uia:ControlType=Button;ClassName=Button;Index=0"
    assert len(locator.find(t2, "uia:Name=Go;ControlType=Button")) == 2


def test_locator_forms() -> None:
    sel = "uia:AutomationId=txtCase;ControlType=Edit"
    ui = locator.to_uipath(sel, "uia:AutomationId=MainForm;ControlType=Window", "CaseDesk.exe")
    assert (
        ui
        == "<wnd app='casedesk.exe' ctrlname='MainForm' /><uia automationid='txtCase' role='editable text' />"
    )
    assert locator.to_pywinauto(sel) == {"auto_id": "txtCase", "control_type": "Edit"}
    assert locator.to_winappdriver(sel) == {"using": "accessibility id", "value": "txtCase"}
    jab = "jab:Name=Save;Role=push button"
    assert locator.to_uipath(
        jab, "jab:Name=Case;Role=dialog;ClassName=SunAwtDialog", "java.exe"
    ) == (
        "<wnd app='java.exe' title='Case' cls='SunAwtDialog' /><java role='push button' name='Save' />"
    )
    assert locator.forms(jab)["pywinauto"] is None


def test_java_locators_and_window_anchor() -> None:
    raw = {"type": "Window", "role": "dialog", "name": "Sign in", "cls": "SunAwtDialog", "children": [
        {"type": "Edit", "role": "password text", "name": "Password", "password": True},
        {"type": "Button", "role": "push button", "name": "Sign in"}]}  # fmt: skip
    t = privacy.clean(raw)
    w = locator.window_locator(t)
    assert w["selector"] == "jab:Name=Sign in;Role=dialog;ClassName=SunAwtDialog;Has=password text"
    box = privacy.clean(
        {**raw, "message_box": "alert", "children": [{"type": "Custom", "role": "alert"}]}
    )
    assert locator.window_locator(box)["selector"].endswith("Has=alert")
    assert locator.matches(box, locator.parse("jab:Role=dialog;Has=alert"))
    assert not locator.matches(t, locator.parse("jab:Role=dialog;Has=alert"))
    n, a, i = list(locator.walk(t))[2]
    assert locator.build(t, n, a, i)["selector"] == "jab:Name=Sign in;Role=push button"


def test_detection_per_toolkit() -> None:
    cases = {
        "WinForms": {
            "framework_ids": {"WinForm", "Win32"},
            "classes": {"WindowsForms10.Window.8.app.0.*"},
        },
        "WPF": {"framework_ids": {"WPF"}, "classes": {"HwndWrapper[App;;x]"}},
        "UWP / WinUI": {"framework_ids": {"XAML"}, "modules": ["Microsoft.UI.Xaml.dll"]},
        "Win32 / MFC": {
            "framework_ids": {"Win32"},
            "classes": {"Afx:00400000:8"},
            "modules": ["mfc140u.dll"],
        },
        "Java Swing / AWT": {
            "classes": {"SunAwtFrame"},
            "modules": ["jvm.dll", "WindowsAccessBridge-64.dll"],
        },
        "JavaFX": {"classes": {"GlassWndClass-GlassWindowClass-2"}, "modules": ["glass.dll"]},
        "Electron / CEF": {"classes": {"Chrome_WidgetWin_1"}, "files": ["resources/app.asar"]},
        "Delphi / VCL": {"classes": {"TApplication", "TEdit"}},
        "Qt": {"classes": {"Qt5QWindowIcon"}, "modules": ["Qt5Core.dll"]},
        "VB6": {"classes": {"ThunderRT6FormDC"}, "modules": ["msvbvm60.dll"]},
        "PowerBuilder": {"classes": {"FNWND3125"}, "modules": ["pbvm125.dll"]},
        "Remote session (Citrix / RDP)": {"classes": {"Transparent Windows Client"}},
    }
    for toolkit, sig in cases.items():
        got = detect.detect(sig)
        assert got["toolkit"] == toolkit, (toolkit, got)
        assert got["evidence"] and all(
            {"name", "marker", "confidence"} <= set(e) for e in got["evidence"]
        )
    assert detect.detect(cases["Qt"])["version"] == "5"
    for name in ("Delphi / VCL", "Qt", "VB6", "PowerBuilder", "Remote session (Citrix / RDP)"):
        got = detect.detect(cases[name])
        assert got["supported"] is False and "not supported" in got["problem"]
    assert "no control tree" in detect.detect(cases["Remote session (Citrix / RDP)"])["problem"]
    assert "web recorder" in detect.detect(cases["Electron / CEF"])["problem"]
    java = detect.detect({"classes": {"SunAwtFrame"}, "modules": ["jvm.dll"]})
    assert java["supported"] and "Access Bridge" in java["problem"]
    plain = detect.detect({"framework_ids": {"Win32"}, "classes": {"#32770"}})
    assert plain["toolkit"] == "Win32" and plain["confidence"] == "guess"
    rt = detect.detect({**cases["WinForms"], "runtimes": ["Microsoft.WindowsDesktop.App 6.0.3"]})[
        "runtime"
    ]
    assert rt == {**rt, "name": ".NET", "version": "6.0.3"}


def _window(*children) -> dict:
    return privacy.clean(
        {"type": "Window", "aid": "MainForm", "enabled": True, "children": list(children)}
    )


def test_compare_trees() -> None:
    base = [{"type": "Edit", "aid": "txtSearch", "cls": "WindowsForms10.Edit.app.0.x", "enabled": True},
            {"type": "Button", "aid": "btnOpen", "name": "Open", "enabled": True},
            {"type": "DataGrid", "aid": "dgv", "children": [
                {"type": "Custom", "children": [{"type": "DataItem", "value": "1"}]}]}]  # fmt: skip
    rec = _window(*base)
    assert compare_trees(rec, _window(*base)) == []
    renamed = [base[0], {**base[1], "aid": "btnView"}, base[2]]
    checks = {(d.check, d.element) for d in compare_trees(rec, _window(*renamed))}
    assert ("missing", "uia:AutomationId=btnOpen;ControlType=Button") in checks
    assert ("extra", "uia:AutomationId=btnView;ControlType=Button") in checks
    twice = [*base, {"type": "Edit", "aid": "txtSearch"}]
    assert any(d.check == "ambiguous" for d in compare_trees(rec, _window(*twice)))
    moved = [{**base[0], "cls": "Edit"}, *base[1:]]
    assert any(d.check == "changed" for d in compare_trees(rec, _window(*moved)))
    disabled = [base[0], {**base[1], "enabled": False}, base[2]]
    assert [d.check for d in compare_trees(rec, _window(*disabled))] == ["enabled"]
    empty = [*base[:2], {"type": "DataGrid", "aid": "dgv"}]
    assert any(d.element.endswith("(rows)") for d in compare_trees(rec, _window(*empty)))
    more_rows = [
        *base[:2],
        {"type": "DataGrid", "aid": "dgv", "children": [base[2]["children"][0]] * 3},
    ]
    assert compare_trees(rec, _window(*more_rows)) == []  # rows compare by presence
    assert value_for("X-9999") == "A-1111"


# --- WPF (found on the live reference app, reference/desktop/CaseDeskWpf) --------------------------


def _menu(*case_items: dict) -> dict:
    """A WPF main window: the items of the Case menu are listed under it only while it is open."""
    case = {"type": "MenuItem", "aid": "mnuCase", "name": "Case", "children": list(case_items)}
    bar = {"type": "Menu", "aid": "menuMain", "children": [
        {"type": "MenuItem", "aid": "mnuFile", "name": "File"}, case]}  # fmt: skip
    return _window(bar, {"type": "Button", "aid": "btnOpen", "name": "Open", "enabled": True})


def test_compare_trees_wpf_menu_open_in_one_tree_only() -> None:
    refresh = {"type": "MenuItem", "aid": "mnuRefresh", "name": "Refresh"}
    open_case = {"type": "MenuItem", "aid": "mnuOpenCase", "name": "Open case"}
    # Open in the recording, closed in the live tree, and the other way round: nothing was seen
    # of the items on one side, so they are neither missing nor extra.
    assert compare_trees(_menu(open_case, refresh), _menu()) == []
    assert compare_trees(_menu(), _menu(open_case, refresh)) == []
    # Open in both: a menu that lost or gained an item is still reported.
    checks = {(d.check, d.element) for d in compare_trees(_menu(open_case, refresh), _menu(open_case))}  # fmt: skip
    assert checks == {("missing", "uia:AutomationId=mnuRefresh;ControlType=MenuItem")}
    checks = {(d.check, d.element) for d in compare_trees(_menu(open_case), _menu(open_case, refresh))}  # fmt: skip
    assert checks == {("extra", "uia:AutomationId=mnuRefresh;ControlType=MenuItem")}
    # A menu of the menu bar itself is never excused.
    no_file = _window({"type": "Menu", "aid": "menuMain", "children": [
        {"type": "MenuItem", "aid": "mnuCase", "name": "Case"}]},
        {"type": "Button", "aid": "btnOpen", "name": "Open", "enabled": True})  # fmt: skip
    assert ("missing", "uia:AutomationId=mnuFile;ControlType=MenuItem") in {
        (d.check, d.element) for d in compare_trees(_menu(), no_file)
    }


def test_wpf_popup_window_content() -> None:
    from labctl.desktop.recorder import popup_content

    popup = {"type": "Window", "cls": "Popup", "fw": "WPF"}
    menu = {**popup, "children": [{"type": "MenuItem", "aid": "mnuRefresh", "name": "Refresh"}]}
    combo = {**popup, "children": [{"type": "ListItem", "name": "Open"}]}
    assert popup_content(menu) == {"MenuItem"}
    assert popup_content(combo) == {"ListItem"}
    assert popup_content(popup) == set()
    # A dialog is not a popup, whatever it holds.
    assert popup_content({"type": "Window", "cls": "Window", "children": menu["children"]}) == set()
    assert popup_content(None) == set()


def _recorder():
    """A Recorder without Windows: only the state its event handling needs."""
    from labctl.desktop.recorder import Recorder

    rec = Recorder.__new__(Recorder)
    rec.pending, rec.last_activity, rec.focus, rec.typed_since_focus = [], 0.0, None, False
    rec.last_combo, rec.pending_items, rec.combo_items = None, None, {}
    return rec


def test_recorder_invoke_after_a_key_is_the_key() -> None:
    """WPF raises Invoke for the default button when Enter is pressed: one key step that names
    the button, not a key and a click."""
    search = {"rid": (1, 2), "type": "Button", "aid": "btnSearch"}
    rec = _recorder()
    rec.pending.append({"kind": "key", "key": "Enter", "t": 10.0})
    rec._semantic({"kind": "invoke", "desc": search, "t": 10.05})
    rec._semantic({"kind": "invoke", "desc": search, "t": 10.07})  # WPF raises it again
    assert [a["kind"] for a in rec.pending] == ["key"]
    assert rec.pending[0]["invoked_desc"] == search
    # A mouse click on the button is a click, also right after a key.
    rec = _recorder()
    rec.pending.append({"kind": "key", "key": "Tab", "t": 10.0})
    rec.pending.append({"kind": "click", "desc": search, "t": 10.1, "provisional": True})
    rec._semantic({"kind": "invoke", "desc": search, "t": 10.15})
    assert [(a["kind"], a.get("via")) for a in rec.pending] == [("key", None), ("click", "uia-invoke")]  # fmt: skip
    assert "invoked_desc" not in rec.pending[0]
    # An Invoke long after the key (a script's pattern call) is a click of its own.
    rec = _recorder()
    rec.pending.append({"kind": "key", "key": "Enter", "t": 10.0})
    rec._semantic({"kind": "invoke", "desc": search, "t": 11.0})
    assert [a["kind"] for a in rec.pending] == ["key", "click"]


def test_recorder_typing_in_a_password_field_without_value_events() -> None:
    """WPF's PasswordBox raises no value event: a key typed while it has the focus is the type
    step. Nothing of the key is kept; other fields are left to their value events."""
    box = {"rid": (7,), "type": "Edit", "aid": "txtPassword", "password": True}
    rec = _recorder()
    rec._typing({"kind": "typing", "t": 1.0})  # no focus known: nothing
    rec.focus = {"rid": (8,), "type": "Edit", "aid": "txtUser"}
    rec._typing({"kind": "typing", "t": 1.1})  # not a password field: nothing
    assert rec.pending == []
    rec.focus = box
    for t in (2.0, 2.1, 2.2):
        rec._typing({"kind": "typing", "t": t})
    assert len(rec.pending) == 1
    step = rec.pending[0]
    assert (step["kind"], step["password"], step["shape"], step["via"]) == (
        "type", True, None, "keyboard-hook")  # fmt: skip
    assert set(step) == {"kind", "desc", "shape", "t", "password", "via"} and rec.typed_since_focus
    # A toolkit that does raise value events (WinForms): the same one step, said by the event.
    rec._typed({"kind": "value", "desc": box, "password": True, "t": 2.3})
    assert len(rec.pending) == 1 and rec.pending[0]["via"] == "uia-value-event"
