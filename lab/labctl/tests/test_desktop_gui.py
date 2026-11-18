"""Live desktop tests: build the reference apps, record their scripted flows, verify them.

These start GUI applications, take the foreground and click with the real mouse (guarded: only
inside the reference app's own windows). So they run only when asked for, once per session:

    TWINLAB_DESKTOP_GUI=1 python -m pytest -q -m desktop_gui

and only on Windows with the desktop extra installed (and the .NET SDK, or a JDK for the Java
test). Each application is started at most three times in a session (record, verify, and for
WinForms and WPF one deliberately wrong build); every start has a timeout and a close in
`finally`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CASEDESK = ROOT / "reference" / "desktop" / "CaseDesk"
CASEDESK_WPF = ROOT / "reference" / "desktop" / "CaseDeskWpf"
SWING = ROOT / "reference" / "desktop" / "CaseDeskSwing"

pytestmark = [
    pytest.mark.desktop_gui,
    pytest.mark.skipif(sys.platform != "win32", reason="needs Windows (UI Automation)"),
    pytest.mark.skipif(importlib.util.find_spec("comtypes") is None, reason="desktop extra missing"),
    pytest.mark.skipif(os.environ.get("TWINLAB_DESKTOP_GUI") != "1",
                       reason="starts GUI apps and clicks: set TWINLAB_DESKTOP_GUI=1 to run"),
]  # fmt: skip


def _flow(folder: Path):
    spec = importlib.util.spec_from_file_location(f"flow_{folder.name}", folder / "flow.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def casedesk(tmp_path_factory):
    if shutil.which("dotnet") is None:
        pytest.skip("the .NET SDK is not installed")
    from labctl.desktop import recorder

    flow = _flow(CASEDESK)
    exe = flow.build()
    out = tmp_path_factory.mktemp("desktop") / "casedesk-flow"
    recorder.record(str(exe), out, script=flow.flow, max_seconds=240)  # closes the app itself
    return exe, out


@pytest.fixture(scope="session")
def casedesk_wpf(tmp_path_factory):
    if shutil.which("dotnet") is None:
        pytest.skip("the .NET SDK is not installed")
    from labctl.desktop import recorder

    flow = _flow(CASEDESK_WPF)
    exe = flow.build()
    out = tmp_path_factory.mktemp("desktop") / "casedesk-wpf-flow"
    recorder.record(str(exe), out, script=flow.flow, max_seconds=240)  # closes the app itself
    return exe, out


@pytest.fixture(scope="session")
def swing(tmp_path_factory):
    flow = _flow(SWING)
    if not (flow.JAVA_HOME / "bin" / "javac.exe").is_file():
        pytest.skip(f"no JDK at {flow.JAVA_HOME} (set TWINLAB_JAVA_HOME)")
    from labctl.desktop import java

    cmd = flow.build()
    out = tmp_path_factory.mktemp("desktop") / "casedesk-swing-flow"
    java.record(cmd, out, script=flow.flow, max_seconds=300)
    return cmd, out


def _steps(out: Path) -> list[dict]:
    return [
        json.loads(line) for line in (out / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _no_values(out: Path) -> None:
    text = "".join(p.read_text(encoding="utf-8") for p in out.rglob("*.json*"))
    for value in ("tester", "wrong", "demo", "C-100", "Card resent", "Ada Example", "C-1001"):
        assert value not in text, value


def test_winforms_recording(casedesk) -> None:
    _exe, out = casedesk
    steps = _steps(out)
    actions = {(s["action"], s.get("selector")) for s in steps}
    assert ("type", "uia:AutomationId=txtUser;ControlType=Edit") in actions
    assert ("click", "uia:AutomationId=btnSignIn;ControlType=Button") in actions
    assert ("toggle", "uia:AutomationId=chkUrgent;ControlType=CheckBox") in actions
    assert ("menu", "uia:Name=Refresh;ControlType=MenuItem") in actions
    assert any(s["action"] == "select" and s.get("item") == "Resolved" for s in steps)
    assert any(s["action"] == "key" and s.get("key") == "Enter" for s in steps)
    assert any(s.get("password") for s in steps)
    session = json.loads((out / "desktop.json").read_text(encoding="utf-8"))
    assert session["processes"][0]["technology"]["toolkit"] == "WinForms"
    _no_values(out)


def test_winforms_verify_against_own_recording(casedesk) -> None:
    from labctl.desktop import verify

    exe, out = casedesk
    report = verify.verify(
        str(exe), out, values={"txtPassword": ["wrong", "demo"], "txtSearch": "C-100"}
    )
    assert report.status == "pass", report.markdown()


def test_winforms_verify_finds_a_wrong_build(casedesk, monkeypatch) -> None:
    from labctl.desktop import verify

    exe, out = casedesk
    monkeypatch.setenv("CASEDESK_VARIANT", "drift")  # btnOpen renamed, txtSearch twice
    report = verify.verify(
        str(exe), out, values={"txtPassword": ["wrong", "demo"], "txtSearch": "C-100"}
    )
    checks = {(d.check, d.element) for s in report.steps for d in s.differences}
    assert report.status == "fail"
    assert ("missing", "uia:AutomationId=btnOpen;ControlType=Button") in checks
    assert ("extra", "uia:AutomationId=btnView;ControlType=Button") in checks
    assert ("ambiguous", "uia:AutomationId=txtSearch;ControlType=Edit") in checks


def test_wpf_recording(casedesk_wpf) -> None:
    _exe, out = casedesk_wpf
    steps = _steps(out)
    actions = [(s["action"], s.get("selector")) for s in steps]
    assert ("type", "uia:AutomationId=txtUser;ControlType=Edit") in actions
    assert ("type", "uia:AutomationId=txtSearch;ControlType=Edit") in actions
    assert actions.count(("type", "uia:AutomationId=txtSummary;ControlType=Edit")) == 2
    # Clicks on a button's caption are clicks on the button (WPF draws it with a TextBlock),
    # once each, whether or not the click closed the window.
    assert actions.count(("click", "uia:AutomationId=btnSignIn;ControlType=Button")) == 2
    assert actions.count(("click", "uia:AutomationId=btnSave;ControlType=Button")) == 2
    assert actions.count(("click", "uia:AutomationId=btnOpen;ControlType=Button")) == 1
    toggles = [s for s in steps if s["action"] == "toggle"]
    assert [(s["selector"], s["state"]) for s in toggles] == [
        ("uia:AutomationId=chkUrgent;ControlType=CheckBox", "on")
    ]
    # Menus: the item in the menu bar, the drop-down (a WPF Popup window: a menu, not a window
    # of the application), the item in it.
    for name in ("mnuCase", "mnuRefresh", "mnuFile", "mnuExit"):
        assert ("menu", f"uia:AutomationId={name};ControlType=MenuItem") in actions
    assert any(s["action"] == "menu_open" for s in steps)
    assert not [s for s in steps if "ClassName=Popup" in (s["window"].get("selector") or "")]
    assert {s["window"]["selector"] for s in steps if s["action"] == "menu"} == {
        "uia:AutomationId=MainForm;ControlType=Window"
    }
    combo = "uia:AutomationId=cboStatus;ControlType=ComboBox"
    selects = [s for s in steps if s["action"] == "select" and s.get("item_is") == "code"]
    assert [(s["selector"], s["item"]) for s in selects] == [(combo, "Resolved")]
    expand = next(s for s in steps if s["action"] == "expand" and s["selector"] == combo)
    assert expand["items"] == ["Open", "In progress", "Resolved", "Closed"]
    # Enter pressed the default button: WPF raises Invoke for it, which is the key, not a click.
    keys = [s for s in steps if s["action"] == "key"]
    assert [(s["key"], s.get("invoked")) for s in keys] == [
        ("Enter", "uia:AutomationId=btnSearch;ControlType=Button")
    ]
    assert ("click", "uia:AutomationId=btnSearch;ControlType=Button") not in actions
    # A PasswordBox raises no value event: the key presses are what says it was typed in.
    typed = [s for s in steps if s["selector"] == "uia:AutomationId=txtPassword;ControlType=Edit"]
    assert [(s["action"], s.get("password"), s.get("typed_shape")) for s in typed] == [
        ("type", True, None)
    ] * 2
    assert {"uia:Name=Sign in;ControlType=Window;ClassName=#32770",
            "uia:Name=Case;ControlType=Window;ClassName=#32770"} <= {
        s["window"]["selector"] for s in steps if s["action"] == "window_open"}  # fmt: skip
    session = json.loads((out / "desktop.json").read_text(encoding="utf-8"))
    tech = session["processes"][0]["technology"]
    assert (tech["toolkit"], tech["confidence"], tech["supported"]) == ("WPF", "certain", True)
    assert tech["runtime"]["name"] == ".NET"
    _no_values(out)


def test_wpf_verify_against_own_recording(casedesk_wpf) -> None:
    from labctl.desktop import verify

    exe, out = casedesk_wpf
    report = verify.verify(
        str(exe), out, values={"txtPassword": ["wrong", "demo"], "txtSearch": "C-100"}
    )
    assert report.status == "pass", report.markdown()
    replayed = {(s.action, s.selector): s for s in report.steps}
    for step in (("click", "uia:AutomationId=btnSave;ControlType=Button"),
                 ("select", "uia:AutomationId=cboStatus;ControlType=ComboBox"),
                 ("toggle", "uia:AutomationId=chkUrgent;ControlType=CheckBox"),
                 ("menu", "uia:AutomationId=mnuRefresh;ControlType=MenuItem"),
                 ("key", None)):  # fmt: skip
        assert replayed[step].status == "pass", (step, replayed[step].note)


def test_wpf_verify_finds_a_wrong_build(casedesk_wpf, monkeypatch) -> None:
    from labctl.desktop import verify

    exe, out = casedesk_wpf
    monkeypatch.setenv("CASEDESK_VARIANT", "drift")  # btnOpen renamed, txtSearch twice
    report = verify.verify(
        str(exe), out, values={"txtPassword": ["wrong", "demo"], "txtSearch": "C-100"}
    )
    checks = {(d.check, d.element) for s in report.steps for d in s.differences}
    assert report.status == "fail"
    assert ("missing", "uia:AutomationId=btnOpen;ControlType=Button") in checks
    assert ("extra", "uia:AutomationId=btnView;ControlType=Button") in checks
    assert ("ambiguous", "uia:AutomationId=txtSearch;ControlType=Edit") in checks


def test_swing_recording_and_verify(swing) -> None:
    from labctl.desktop import java

    cmd, out = swing
    steps = _steps(out)
    actions = {(s["action"], s.get("selector")) for s in steps}
    assert ("type", "jab:Name=User name;Role=text") in actions
    assert ("click", "jab:Name=Sign in;Role=push button") in actions
    assert ("toggle", "jab:Name=Urgent;Role=check box") in actions
    assert any(s["action"] == "select" and s.get("item") == "Resolved" for s in steps)
    _no_values(out)
    report = java.verify(cmd, out, values={"Password": ["wrong", "demo"], "Case or name": "C-100"})
    assert report.status == "pass", report.markdown()
