"""Live desktop tests: build the reference apps, record their scripted flows, verify them.

These start GUI applications, take the foreground and click with the real mouse (guarded: only
inside the reference app's own windows). So they run only when asked for, once per session:

    TWINLAB_DESKTOP_GUI=1 python -m pytest -q -m desktop_gui

and only on Windows with the desktop extra installed (and the .NET SDK, or a JDK for the Java
test). Each application is started at most three times in a session (record, verify, and for
WinForms one deliberately wrong build); every start has a timeout and a close in `finally`.
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
