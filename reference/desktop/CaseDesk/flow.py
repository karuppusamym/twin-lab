"""The scripted CaseDesk flow Twin Lab records as evidence, standing in for a person.

It types through UI Automation patterns (Value) and picks the status with SelectionItem and
ExpandCollapse. Buttons and menus are pressed with real mouse clicks (SendInput), because
WinForms raises no Invoke event a recorder could hear for a pattern call: a person's click is
what the recorder's mouse hook sees, so the script makes the same. Each click first checks that
CaseDesk owns the foreground window and the window under the point (labctl.desktop.drive), so
no click can land in another application; the cursor is put back after each click. Enter in
the search box is a real key press, sent only while CaseDesk is in the foreground. Run it from
the repository root:

    python reference/desktop/CaseDesk/flow.py [out-folder]

It builds CaseDesk (`dotnet build -c Release`), starts it, records the flow below with
`labctl.desktop.recorder.record`, and closes it. The default folder is
`evidence/casedesk/casedesk-flow/` (Git-ignored, like all evidence).

Flow: sign in with a wrong password (a message box says so), sign in; search; open the menu
Case > Refresh; open a case; set a too-short summary, choose status Resolved, tick Urgent, Save
(a message box: the summary is too short); write a longer summary, Save; File > Exit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXE = HERE / "bin" / "Release" / "net6.0-windows" / "CaseDesk.exe"

# Values typed by the script: synthetic, and never stored (the recording keeps their shapes).
USER, BAD_PASSWORD, PASSWORD = "tester", "wrong", "demo"


def build() -> Path:
    subprocess.run(["dotnet", "build", "-c", "Release", "-nologo", "-v", "q"], cwd=HERE,
                   check=True, capture_output=True)  # fmt: skip
    return EXE


def flow(pids: set[int], pause: float = 0.6) -> None:
    from labctl.desktop.drive import Driver

    d = Driver(pids, pause)
    w = d.window("uia:AutomationId=LoginForm;ControlType=Window")
    d.type(w, "uia:AutomationId=txtUser;ControlType=Edit", USER)
    d.type(w, "uia:AutomationId=txtPassword;ControlType=Edit", BAD_PASSWORD)
    d.click(w, "uia:AutomationId=btnSignIn;ControlType=Button")
    box = d.window("uia:Name=Sign in;ControlType=Window;ClassName=#32770")
    d.click(box, "uia:Name=OK;ControlType=Button")
    d.type(w, "uia:AutomationId=txtPassword;ControlType=Edit", PASSWORD)
    d.click(w, "uia:AutomationId=btnSignIn;ControlType=Button")

    main = d.window("uia:AutomationId=MainForm;ControlType=Window")
    d.type(main, "uia:AutomationId=txtSearch;ControlType=Edit", "C-100")
    if not d.key("Enter"):  # Enter presses Search (the form's AcceptButton)
        d.click(main, "uia:AutomationId=btnSearch;ControlType=Button")
    d.click_menu(main, "Case", "Refresh")
    d.click(main, "uia:AutomationId=btnOpen;ControlType=Button")

    case = d.window("uia:AutomationId=CaseDetailForm;ControlType=Window")
    d.type(case, "uia:AutomationId=txtSummary;ControlType=Edit", "Done")
    d.choose(case, "uia:AutomationId=cboStatus;ControlType=ComboBox", "Resolved")
    d.click(case, "uia:AutomationId=chkUrgent;ControlType=CheckBox")
    d.click(case, "uia:AutomationId=btnSave;ControlType=Button")
    box = d.window("uia:Name=Case;ControlType=Window;ClassName=#32770")
    d.click(box, "uia:Name=OK;ControlType=Button")
    d.type(case, "uia:AutomationId=txtSummary;ControlType=Edit", "Card resent by courier")
    d.click(case, "uia:AutomationId=btnSave;ControlType=Button")

    d.click_menu(main, "File", "Exit")


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT / "lab" / "labctl"))
    from labctl.desktop import recorder

    out = Path(argv[1]) if len(argv) > 1 else ROOT / "evidence" / "casedesk" / "casedesk-flow"
    exe = build()
    recorder.record(str(exe), out, script=flow)
    print(
        f"recorded {sum(1 for _ in (out / 'steps.jsonl').open(encoding='utf-8'))} step(s) in {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
