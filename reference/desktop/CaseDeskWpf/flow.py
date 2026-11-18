"""The scripted CaseDeskWpf flow Twin Lab records as evidence, standing in for a person.

The same flow as `../CaseDesk/flow.py`, against the WPF build of the application: it types
through the Value pattern and picks the status with ExpandCollapse and SelectionItem. Buttons,
the check box and menus are pressed with real mouse clicks (SendInput), as a person's hand
would: each click first checks that CaseDeskWpf owns the foreground window and the window under
the point (labctl.desktop.drive), so no click can land in another application; the cursor is
put back after each click. The password is typed with real key presses, because WPF's
PasswordBox raises no value event when a pattern sets it: a person's key presses are the only
thing a recorder can notice there. They and Enter in the search box are sent only while
CaseDeskWpf is in the foreground (and the password box has the keyboard focus). Run it from the
repository root:

    python reference/desktop/CaseDeskWpf/flow.py [out-folder]

It builds CaseDeskWpf (`dotnet build -c Release`), starts it, records the flow below with
`labctl.desktop.recorder.record`, and closes it. The default folder is
`evidence/casedesk-wpf/casedesk-wpf-flow/` (Git-ignored, like all evidence).

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
EXE = HERE / "bin" / "Release" / "net6.0-windows" / "CaseDeskWpf.exe"

# Values typed by the script: synthetic, and never stored (the recording keeps their shapes).
USER, BAD_PASSWORD, PASSWORD = "tester", "wrong", "demo"


def build() -> Path:
    subprocess.run(["dotnet", "build", "-c", "Release", "-nologo", "-v", "q"], cwd=HERE,
                   check=True, capture_output=True)  # fmt: skip
    return EXE


def flow(pids: set[int], pause: float = 0.6) -> None:
    from labctl.desktop.drive import Driver

    d = Driver(pids, pause)
    # A pause after each window opens, as a person looks before typing: the recorder reads a
    # new window within its poll (250 ms), and takes a value set before that as the
    # application filling the window in.
    w = d.window("uia:AutomationId=LoginForm;ControlType=Window")
    d.pause()
    d.type(w, "uia:AutomationId=txtUser;ControlType=Edit", USER)
    d.keys(w, "uia:AutomationId=txtPassword;ControlType=Edit", BAD_PASSWORD)
    d.click(w, "uia:AutomationId=btnSignIn;ControlType=Button")
    box = d.window("uia:Name=Sign in;ControlType=Window;ClassName=#32770")
    d.click(box, "uia:Name=OK;ControlType=Button")
    d.keys(w, "uia:AutomationId=txtPassword;ControlType=Edit", PASSWORD)
    d.click(w, "uia:AutomationId=btnSignIn;ControlType=Button")

    main = d.window("uia:AutomationId=MainForm;ControlType=Window")
    d.pause()
    d.type(main, "uia:AutomationId=txtSearch;ControlType=Edit", "C-100")
    if not d.key("Enter"):  # Enter presses Search (the window's default button)
        d.click(main, "uia:AutomationId=btnSearch;ControlType=Button")
    d.click_menu(main, "Case", "Refresh")
    d.click(main, "uia:AutomationId=btnOpen;ControlType=Button")

    case = d.window("uia:AutomationId=CaseDetailForm;ControlType=Window")
    d.pause()
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

    out = (
        Path(argv[1]) if len(argv) > 1 else ROOT / "evidence" / "casedesk-wpf" / "casedesk-wpf-flow"
    )
    exe = build()
    recorder.record(str(exe), out, script=flow)
    print(
        f"recorded {sum(1 for _ in (out / 'steps.jsonl').open(encoding='utf-8'))} step(s) in {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
