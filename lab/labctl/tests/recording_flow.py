"""The scripted flow that produced fixtures/showcase-rec (synthetic data only).

Regenerate the fixture against a running showcase demo copy (port 8720) with:

    python lab/labctl/tests/recording_flow.py lab/labctl/tests/fixtures/showcase-rec

It changes no data: both saves are rejected by the application's rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE = "http://localhost:8720"
USER, PASSWORD = "demo.user", "workbench-demo"  # the demo copy's synthetic sign-in


def showcase_flow(page) -> None:
    page.fill("#username", USER)
    page.fill("#password", "not-the-password")
    page.click("#btnLogin")
    page.wait_for_selector("#loginError")
    page.fill("#password", PASSWORD)
    page.click("#btnLogin")
    page.wait_for_url("**/cases")
    page.fill("#caseNo", "C-1001")
    page.click("#btnSearch")
    page.wait_for_selector("table#results td.status")
    page.click("a.case-link")
    page.wait_for_selector("select#category option:nth-child(2)", state="attached")
    page.select_option("#category", index=1)
    page.wait_for_selector("select#subcategory option:nth-child(2)", state="attached")
    page.select_option("#subcategory", index=1)
    page.select_option("#caseStatus", "RESOLVED")
    page.click("#btnSave")  # rejected: a note is needed first
    page.wait_for_selector("#message.error")
    page.select_option("#caseStatus", "ESCALATED")
    page.fill("#escalationReason", "short")
    page.click("#btnSave")  # rejected: the reason is too short
    page.wait_for_timeout(600)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from labctl.recorder import record

    record(f"{BASE}/login", Path(sys.argv[1]), script=showcase_flow, headless=True)
