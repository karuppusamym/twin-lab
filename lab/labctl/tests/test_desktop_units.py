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
    # no value is held: only the action, its shape and its capture id and time
    assert set(step) == {"kind", "desc", "shape", "t", "password", "via", "event_id",
                         "captured_at"} and rec.typed_since_focus  # fmt: skip
    # A toolkit that does raise value events (WinForms): the same one step, said by the event.
    rec._typed({"kind": "value", "desc": box, "password": True, "t": 2.3})
    assert len(rec.pending) == 1 and rec.pending[0]["via"] == "uia-value-event"


def test_recorder_click_is_placed_at_the_mouse_press_before_the_window_it_opens() -> None:
    """The Invoke event of a clicked button arrives late (the window poll may already have seen
    the message box the click opened): the click step keeps the time of the mouse press."""
    from labctl.desktop.recorder import order_batch

    save = {"rid": (1, 9), "type": "Button", "aid": "btnSave", "window": 100}
    rec = _recorder()
    rec.pending.append({"kind": "click", "desc": save, "t": 10.0, "provisional": True,
                        "injected": True})  # fmt: skip
    rec.pending.append({"kind": "window_open", "hwnd": 200, "owner": 100, "t": 10.2})
    rec._semantic({"kind": "invoke", "desc": save, "t": 10.3})
    batch = order_batch(rec.pending)
    assert [a["kind"] for a in batch] == ["click", "window_open"]
    assert (batch[0]["t"], batch[0]["via"], batch[0]["injected"]) == (10.0, "uia-invoke", True)
    # The same event raised again is still the same click (compared with the event's time).
    rec._semantic({"kind": "invoke", "desc": save, "t": 10.35})
    assert [a["kind"] for a in rec.pending].count("click") == 1
    # A click by a pattern call has no mouse press: its Invoke event, handled after the poll saw
    # the window it opened, goes before that window when the click was in the window's owner.
    win_open = {"kind": "window_open", "hwnd": 200, "owner": 100, "t": 10.2}
    invoke = {"kind": "click", "desc": save, "t": 10.3, "via": "uia-invoke"}
    assert [a["kind"] for a in order_batch([win_open, invoke])] == ["click", "window_open"]
    # Not a click in another window, and not one long after the window opened.
    other = {"kind": "click", "desc": {**save, "window": 300}, "t": 10.3, "via": "uia-invoke"}
    late = {"kind": "click", "desc": save, "t": 11.5, "via": "uia-invoke"}
    for click in (other, late):
        batch = order_batch([{**win_open}, click])
        assert [a["kind"] for a in batch] == ["window_open", "click"]


GRID, ROW, CELL, SAVE = (1, 2), (1, 2, 5), (1, 2, 5, 0), (1, 9)


def _row(rid=ROW) -> dict:
    return {"rid": rid, "type": "DataItem", "window": 100, "ancestors": [GRID, (1,)],
            "ancestor_types": ["DataGrid", "Window"]}  # fmt: skip


def test_selection_the_application_makes_is_not_a_user_step() -> None:
    from labctl.desktop.recorder import self_made_selection

    save = {"rid": SAVE, "type": "Button", "ancestors": [(1,)]}
    cell = {"rid": CELL, "type": "Text", "ancestors": [ROW, GRID, (1,)]}
    other_cell = {"rid": (1, 2, 6, 0), "type": "Text", "ancestors": [(1, 2, 6), GRID, (1,)]}
    assert self_made_selection(_row(), save)  # Save pressed, the grid selects its row again
    assert not self_made_selection(_row(), cell)  # the mouse was pressed in the row
    assert not self_made_selection(_row(), _row())  # ... or on the row itself
    assert not self_made_selection(_row(), other_cell)  # an arrow key, focus in the same grid
    assert not self_made_selection(_row(), {"rid": GRID, "ancestors": [(1,)]})
    assert not self_made_selection(_row(), None)  # nothing known: a step, as before
    assert not self_made_selection(_row(), {})
    assert not self_made_selection(_row(), save, popup=True)  # a drop-down list
    radio = {"rid": (1, 4, 1), "type": "RadioButton", "ancestors": [(1, 4), (1,)],
             "ancestor_types": ["Group", "Window"]}  # fmt: skip
    sibling = {"rid": (1, 4, 0), "type": "RadioButton", "ancestors": [(1, 4), (1,)]}
    assert not self_made_selection(radio, sibling)  # the next radio button, by an arrow key
    assert self_made_selection(radio, save)


def test_recorder_keeps_a_self_made_selection_as_an_effect_of_its_cause() -> None:
    save = {"rid": SAVE, "type": "Button", "window": 100, "ancestors": [(1,)]}
    rec = _recorder()
    rec.pending.append({"kind": "click", "desc": save, "t": 10.0, "provisional": True})
    rec._user(save, 10.0, rec.pending[-1])
    rec._semantic({"kind": "select", "desc": _row(), "t": 10.1})
    assert [a["kind"] for a in rec.pending] == ["click"]
    assert [(e["kind"], e["desc"]["rid"]) for e in rec.pending[0]["effects"]] == [("select", ROW)]
    # The click's own Invoke event replaces the provisional click: the effect stays with it.
    rec._semantic({"kind": "invoke", "desc": save, "t": 10.15})
    rec._semantic({"kind": "select", "desc": _row(), "t": 10.2})  # raised twice: kept once
    assert [(a["kind"], len(a["effects"])) for a in rec.pending] == [("click", 1)]
    # Its cause was written already: counted for the session's notes, still no step.
    rec.pending = []
    rec._semantic({"kind": "select", "desc": _row((1, 2, 6)), "t": 12.0})
    assert rec.pending == [] and rec.effects_unplaced == 1
    # The user presses the mouse in a row, or a key while the grid has the focus: a select step.
    cell = {"rid": CELL, "type": "Text", "window": 100, "ancestors": [ROW, GRID, (1,)]}
    rec.pending.append({"kind": "click", "desc": cell, "t": 13.0, "provisional": True})
    rec._user(cell, 13.0, rec.pending[-1])
    rec._semantic({"kind": "select", "desc": _row(), "t": 13.1})
    assert [(a["kind"], a.get("via")) for a in rec.pending] == [("select", "uia-selection")]
    rec.pending = []
    rec._user(cell, 14.0)  # an arrow key: no step, only that the user pressed a key there
    rec._semantic({"kind": "select", "desc": _row((1, 2, 6)), "t": 14.1})
    assert [a["kind"] for a in rec.pending] == ["select"]


def _java_window(status: str, urgent: str = "off", menu_open: bool = False) -> dict:
    kids = [
        {"type": "ComboBox", "role": "combo box", "name": "Status", "value": status,
         "rect": [10, 10, 80, 20]},
        {"type": "CheckBox", "role": "check box", "name": "Urgent", "toggle": urgent,
         "rect": [10, 40, 80, 20]},
        {"type": "Button", "role": "push button", "name": "OK", "rect": [10, 70, 80, 20]},
    ]  # fmt: skip
    if menu_open:  # a lightweight popup is put in front of the others: every path moves
        kids.insert(0, {"type": "Menu", "role": "popup menu", "name": None, "rect": [0, 0, 5, 5]})
    for i, k in enumerate(kids):
        k.update({"rid": (i,), "fw": "Java", "visible": True})
    return {"type": "Window", "role": "dialog", "name": "Case", "rid": (), "fw": "Java",
            "visible": True, "rect": [0, 0, 100, 100], "children": kids}  # fmt: skip


def _java_recorder(gone: bool = False):
    """A JavaRecorder without Windows or a JVM: one window (100), read once."""
    from types import SimpleNamespace

    from labctl.desktop.java import JavaRecorder

    rec = JavaRecorder.__new__(JavaRecorder)
    rec.pending, rec.known, rec.structure, rec.last_activity = [], set(), set(), 0.0
    user32 = SimpleNamespace(IsWindowVisible=lambda h: not gone)
    rec.win = SimpleNamespace(window_pid=lambda h: 0 if gone else 7, user32=user32)
    rec._rect = lambda h: None if gone else (500, 300, 600, 400)
    raw = _java_window("Open")
    raw["_screen"] = (500, 300)
    rec.trees = {100: (raw, raw)}
    return rec


def _answer(value):
    from concurrent.futures import Future

    fut: Future = Future()
    fut.set_result(value)
    return fut


def test_java_change_seen_when_a_click_arrives_is_placed_before_the_click() -> None:
    """A combo box item chosen (no mouse, no key) and then a click on the check box: the tree
    read when the click arrived already shows the item, so the select goes before the click."""
    from labctl.desktop.java import input_made, place_changes, same_structure

    urgent = {"rid": (1,), "type": "CheckBox", "window": 100, "ancestor_types": []}
    rec = _java_recorder()
    rec._before_input(100, _answer(_java_window("Resolved", "on")), urgent, 50.0)
    assert [(a["kind"], a["desc"]["name"], a["t"] < 50.0) for a in rec.pending] == [
        ("select", "Status", True)  # and no toggle: the click on the check box is that step
    ]
    assert rec.trees[100][0]["children"][0]["value"] == "Resolved"  # not found a second time
    assert rec._changes(100, rec.trees[100], rec.trees[100], covered=False) == []
    # The input made the change (a click on an item of the combo box's list): after it.
    combo = {"rid": (0,), "type": "ComboBox"}
    item = {"rid": (0, 0, 2), "type": "ListItem", "ancestor_types": ["List", "ComboBox"]}
    select = {"kind": "select", "desc": combo, "t": 0.0}
    assert input_made(combo, item) and input_made(combo, combo) and not input_made(combo, urgent)
    assert not input_made(combo, None)
    assert place_changes([select], item, 50.0)[0]["t"] > 50.0
    assert place_changes([select], None, 50.0)[0]["t"] < 50.0  # a key, focus not known
    # The click had its effect before the tree was read and moved the controls (a menu
    # opened): controls are known by their path, so that tree is not used at all.
    rec = _java_recorder()
    before = rec.trees[100]
    shifted = _java_window("Resolved", menu_open=True)
    assert not same_structure(before[0], shifted) and same_structure(before[0], before[0])
    rec._before_input(100, _answer(shifted), None, 50.0)
    assert rec.pending == [] and rec.trees[100] is before
    rec._before_input(100, _answer(None), None, 50.0)  # the window closed while it was read
    assert rec.pending == [] and rec.trees[100] is before


def test_java_click_that_closes_its_window_is_still_located() -> None:
    """The OK button of a message box: by the time the click is handled the window is gone.
    The control is found in the window's last tree, by where the window was when the mouse
    was pressed (read in the hook); a tree read while the window closes is never kept."""
    from types import SimpleNamespace

    rec = _java_recorder(gone=True)
    hit = rec._hit(100, 550, 380, (500, 300, 600, 400))
    assert (hit["name"], hit["type"], hit["window"]) == ("OK", "Button", 100)
    assert rec._hit(100, 550, 380)["name"] == "OK"  # no rectangle from the hook: where it was
    assert rec._hit(100, 900, 900, (500, 300, 600, 400)) is None
    # The window closes while the bridge reads it: no snapshot, the last tree stays.
    closing = _java_window("Open")
    for kid in closing["children"]:
        kid.update({"visible": False, "rect": [0, 0, 0, 0]})
    rec = _java_recorder()
    rec.pids, rec.jab = {7}, SimpleNamespace(capture=None)
    rec.bridge = SimpleNamespace(call=lambda fn, hwnd: closing)
    rec.win.user32.IsWindowVisible = lambda h: False  # gone when the tree came back
    assert rec._snapshot(100) is None
    rec.win.user32.IsWindowVisible = lambda h: True
    assert rec._snapshot(100)[0]["_screen"] == (500, 300)


def test_java_tree_read_while_the_window_changes_is_not_kept(monkeypatch) -> None:
    """A tree read while a click takes effect (a menu opening) can lack whole branches: the
    click that is waiting must not be located in it."""
    import queue
    from types import SimpleNamespace

    from labctl.desktop import jab

    # Read when idle, and an input arrived meanwhile: the last tree stays.
    rec = _java_recorder()
    before, rec.q = rec.trees[100], queue.Queue()
    rec.windows, rec.win.foreground = {100}, lambda: 100
    rec._snapshot = lambda h: ({"type": "Window", "rid": (), "_screen": (500, 300)},) * 2
    rec.q.put({"kind": "click", "x": 550, "y": 380, "t": 1.0})
    rec._idle_diff()
    assert rec.trees[100] is before and rec._hit(100, 550, 380)["name"] == "OK"
    rec.q.get()
    rec._idle_diff()  # nothing arrived: the new tree is the window's tree
    assert rec.trees[100] is not before
    # The bridge: a reading in which a child could not be read is torn and is read again; a
    # component that never gives a child it announces is accepted at the last try.
    whole = {"type": "Window", "children": [{"type": "Button"}]}
    readings = [({"type": "Window"}, True), (whole, False), ({"type": "Pane"}, False)]
    monkeypatch.setattr(jab, "_capture", lambda b, hwnd: readings.pop(0))
    monkeypatch.setattr(jab.time, "sleep", lambda s: None)
    assert jab.capture(SimpleNamespace(), 100) is whole and len(readings) == 1
    readings[:] = [({"type": "Window", "n": i}, True) for i in range(5)]
    assert jab.capture(SimpleNamespace(), 100)["n"] == jab.CAPTURE_TRIES - 1
    readings[:] = [(None, False), (whole, False)]
    assert jab.capture(SimpleNamespace(), 100) is None  # the window is gone: not read again
