"""The scripted CaseDeskSwing flow Twin Lab records as evidence (Java Access Bridge path).

Text goes in through the bridge (`setTextContents`), the status is chosen through the bridge's
selection call, and buttons, menus and the check box get real mouse clicks (SendInput) that
first check CaseDeskSwing owns the foreground window and the window under the point, so no
click lands anywhere else; the cursor is put back after each click.

    python reference/desktop/CaseDeskSwing/flow.py [out-folder]

Compiles the app with `javac` (JDK 11; set TWINLAB_JAVA_HOME to use another), starts it with the
Access Bridge loaded for that run only (no `jabswitch`, nothing persistent changes), records,
and closes it. Default folder: `evidence/casedesk-swing/casedesk-swing-flow/` (Git-ignored).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
JAVA_HOME = Path(
    os.environ.get("TWINLAB_JAVA_HOME", r"C:\Program Files\Microsoft\jdk-11.0.12.7-hotspot")
)
USER, BAD_PASSWORD, PASSWORD = "tester", "wrong", "demo"  # synthetic, never stored
STATUS_RESOLVED = 2  # index of "Resolved" in the status combo box

LOGIN = "jab:Name=Sign in;Role=dialog;Has=password text"
MESSAGE = "jab:Role=dialog;Has=alert"  # a JOptionPane warning
MAIN = "jab:Name=CaseDesk - Cases;Role=frame"
DETAIL = "jab:Role=dialog;Has=combo box"


def build() -> list[str]:
    out = HERE / "build"
    out.mkdir(exist_ok=True)
    subprocess.run([str(JAVA_HOME / "bin" / "javac.exe"), "-d", str(out),
                    str(HERE / "src" / "CaseDeskSwing.java")], check=True, timeout=120)  # fmt: skip
    from labctl.desktop.java import java_command

    return java_command(str(JAVA_HOME / "bin" / "java.exe"), str(out), "CaseDeskSwing")


def flow(pids: set[int], pause: float = 0.7) -> None:
    from labctl.desktop.java import JavaDriver

    d = JavaDriver(pids, pause)
    w = d.window(LOGIN, timeout=20)
    d.type(w, "jab:Name=User name;Role=text", USER)
    d.type(w, "jab:Name=Password;Role=password text", BAD_PASSWORD)
    d.click(w, "jab:Name=Sign in;Role=push button")
    box = d.window(MESSAGE)
    d.click(box, "jab:Name=OK;Role=push button")
    d.type(w, "jab:Name=Password;Role=password text", PASSWORD)
    d.click(w, "jab:Name=Sign in;Role=push button")

    main = d.window(MAIN)
    d.type(main, "jab:Name=Case or name;Role=text", "C-100")
    d.click(main, "jab:Name=Search;Role=push button")
    d.click(main, "jab:Name=Case;Role=menu")
    d.click(main, "jab:Name=Refresh;Role=menu item", scope=d.win.top_windows(d.pids))
    d.click(main, "jab:Name=Open;Role=push button")

    case = d.window(DETAIL)
    d.type(case, "jab:Name=Summary;Role=text", "Done")
    d.choose(case, "jab:Name=Status;Role=combo box", STATUS_RESOLVED)
    d.click(case, "jab:Name=Urgent;Role=check box")
    d.click(case, "jab:Name=Save;Role=push button")
    box = d.window(MESSAGE)
    d.click(box, "jab:Name=OK;Role=push button")
    d.type(case, "jab:Name=Summary;Role=text", "Card resent by courier")
    d.click(case, "jab:Name=Save;Role=push button")

    d.click(main, "jab:Name=File;Role=menu")
    d.click(main, "jab:Name=Exit;Role=menu item", scope=d.win.top_windows(d.pids))


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT / "lab" / "labctl"))
    from labctl.desktop import java

    out = (
        Path(argv[1])
        if len(argv) > 1
        else ROOT / "evidence" / "casedesk-swing" / "casedesk-swing-flow"
    )
    java.record(build(), out, script=flow, max_seconds=300)
    print(
        f"recorded {sum(1 for _ in (out / 'steps.jsonl').open(encoding='utf-8'))} step(s) in {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
