"""Record the QTT sample's flows as evidence: a HAR, the DOM after every step, and a step log.

    python tools/record_qtt.py                # reset the sample's database, start it, record all
    python tools/record_qtt.py --no-reset     # record against a sample already running on --base
    python tools/record_qtt.py --flow loan-search

Writes, per flow, into evidence/qtt/<flow>/ (Git-ignored, never committed):

    recording.har   HAR 1.2 with response bodies embedded (Playwright record_har_path)
    dom/0001.html   document.documentElement.outerHTML after each step
    steps.jsonl     {"seq", "ts", "url", "action", "selector", "dom"} per step
                    action is goto | click | type | select | submit

and copies reference/qtt-sample/db/ddl-sqlserver.sql to evidence/qtt/ddl.sql.

By default the script deletes reference/qtt-sample/data/qtt.db and starts the sample itself on
the port of --base, so every run records from the same seed; stop any sample you started by hand
first. Synthetic data only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "reference" / "qtt-sample"
DB_FILE = SAMPLE / "data" / "qtt.db"
DDL = SAMPLE / "db" / "ddl-sqlserver.sql"
PASSWORD = (
    "Qtt-Sample-2026"  # the sample's synthetic test password (reference/qtt-sample/README.md)
)

CHECKLIST = [
    "CRD-01", "CRD-02", "CRD-03", "INC-01", "INC-02", "INC-03",
    "COL-01", "COL-02", "COL-03", "CMP-01", "CMP-02", "CMP-03",
]  # fmt: skip
PASS_COMMENT = "Checked against the loan file."
FAIL_ITEM = "INC-01"
FAIL_COMMENT = "Only one year of W-2s in the file; second year missing."


class Recorder:
    """Drives one page and writes a step log and a DOM snapshot after every step."""

    def __init__(self, page: Page, base: str, folder: Path):
        self.page, self.base, self.folder = page, base.rstrip("/"), folder
        self.seq = 0
        (folder / "dom").mkdir(parents=True, exist_ok=True)
        self.log = (folder / "steps.jsonl").open("w", encoding="utf-8")

    def close(self) -> None:
        self.log.close()

    def _settle(self) -> None:
        self.page.wait_for_load_state("load")
        self.page.wait_for_load_state("networkidle")

    def _record(self, action: str, selector: str | None) -> None:
        self.seq += 1
        dom = f"dom/{self.seq:04d}.html"
        html = self.page.evaluate("document.documentElement.outerHTML")
        (self.folder / dom).write_text(html, encoding="utf-8")
        step = {
            "seq": self.seq,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "url": self.page.url,
            "action": action,
            "selector": selector,
            "dom": dom,
        }
        self.log.write(json.dumps(step) + "\n")

    def goto(self, path: str) -> None:
        self.page.goto(self.base + path)
        self._settle()
        self._record("goto", None)

    def type(self, selector: str, text: str) -> None:
        self.page.fill(selector, text)
        self._record("type", selector)

    def select(self, selector: str, label: str) -> None:
        self.page.select_option(selector, label=label)
        self._record("select", selector)

    def submit(self, selector: str) -> None:
        """Click a button that submits a form and loads a new page."""
        with self.page.expect_navigation():
            self.page.click(selector)
        self._settle()
        self._record("submit", selector)

    def click(self, selector: str, api: str | None = None, navigates: bool = False) -> None:
        """Click; wait for the fetch call whose path contains `api`, or for the new page."""
        if navigates:
            with self.page.expect_navigation():
                self.page.click(selector)
        elif api:
            with self.page.expect_response(lambda r: api in urlsplit(r.url).path):
                self.page.click(selector)
            self.page.wait_for_timeout(150)  # let the page redraw from the response
        else:
            self.page.click(selector)
        self._settle()
        self._record("click", selector)

    # -- common steps --

    def sign_in(self, username: str, password: str = PASSWORD) -> None:
        self.goto("/login")
        self.type("#username", username)
        self.type("#password", password)
        self.submit("#btnLogin")

    def sign_out(self) -> None:
        self.goto("/logout")


# --- flows -------------------------------------------------------------------------------------


def qc_review(r: Recorder) -> None:
    """Analyst finds an OPEN review in the queue, answers every item (one Fail) and submits."""
    review = "R-1001"
    r.sign_in("analyst1")
    r.select("#statusFilter", "OPEN")
    r.type("#reviewSearch", review)
    r.submit("#btnFilter")
    r.click(f"#open-{review}", navigates=True)
    for item in CHECKLIST:
        fail = item == FAIL_ITEM
        r.type(f"#comment-{item}", FAIL_COMMENT if fail else PASS_COMMENT)
        r.select(f"#result-{item}", "Fail" if fail else "Pass")
        r.click(f"#save-{item}", api=f"/items/{item}")
    r.click("#btnSubmit", api="/submit")
    r.goto("/queue?status=SUBMITTED&mine=1")


def review_return(r: Recorder) -> None:
    """Reviewer returns a submitted review with a reason; the analyst then sees it RETURNED."""
    review = "R-1003"
    r.sign_in("reviewer1")
    r.select("#statusFilter", "SUBMITTED")
    r.submit("#btnFilter")
    r.click(f"#open-{review}", navigates=True)
    r.type("#returnReason", "Please attach the DTI recalculation worksheet for INC-03.")
    r.click("#btnReturn", api="/return")
    r.sign_out()
    r.sign_in("analyst1")
    r.select("#statusFilter", "RETURNED")
    r.click("#mineOnly")
    r.submit("#btnFilter")
    r.click(f"#open-{review}", navigates=True)


def loan_search(r: Recorder) -> None:
    """Analyst searches loans by borrower name and by loan number, opens one, finds nothing."""
    r.sign_in("analyst1")
    r.goto("/loans")
    r.type("#loanQuery", "holt")
    r.submit("#btnSearch")
    r.click("#loan-L-200001", navigates=True)
    r.click("#backToSearch", navigates=True)
    r.type("#loanQuery", "L-20004")
    r.submit("#btnSearch")
    r.type("#loanQuery", "no such borrower")
    r.submit("#btnSearch")


def error_cases(r: Recorder) -> None:
    """The rules' error responses: wrong password, unknown review, submit with items missing,
    Fail without a comment, and a return reason that is too short."""
    r.goto("/login")
    r.type("#username", "analyst1")
    r.type("#password", "not-the-password")
    r.submit("#btnLogin")
    r.sign_in("analyst1")
    r.goto("/reviews/R-9999")
    r.goto("/reviews/R-1011")
    r.click("#btnSubmit", api="/submit")  # 422 ITEMS_INCOMPLETE
    r.select("#result-CRD-01", "Fail")
    r.click("#save-CRD-01", api="/items/CRD-01")  # 422 COMMENT_REQUIRED
    r.type("#comment-CRD-01", "Credit report is 134 days old at the note date.")
    r.click("#save-CRD-01", api="/items/CRD-01")  # saved
    r.click("#btnSubmit", api="/submit")  # 422 ITEMS_INCOMPLETE, 11 missing
    r.sign_out()
    r.sign_in("reviewer1")
    r.goto("/reviews/R-1007")
    r.type("#returnReason", "Fix INC-03")
    r.click("#btnReturn", api="/return")  # 422 RETURN_REASON_REQUIRED
    r.goto("/reviews/R-1005")  # approved and locked: controls disabled


FLOWS = {
    "qc-review": qc_review,
    "review-return": review_return,
    "loan-search": loan_search,
    "error-cases": error_cases,
}


# --- running the sample ------------------------------------------------------------------------


def healthy(base: str) -> bool:
    try:
        return httpx.get(base + "/api/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def start_sample(base: str) -> subprocess.Popen:
    if healthy(base):
        sys.exit(f"a sample is already running at {base}; stop it first, or pass --no-reset")
    DB_FILE.unlink(missing_ok=True)
    port = str(urlsplit(base).port or 8730)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", port, "--log-level", "warning"],
        cwd=SAMPLE,
    )
    for _ in range(100):
        if healthy(base):
            return proc
        if proc.poll() is not None:
            sys.exit(f"the sample did not start (exit code {proc.returncode})")
        time.sleep(0.2)
    proc.terminate()
    sys.exit("the sample did not answer /api/health within 20 seconds")


def record(base: str, out: Path, names: list[str], headed: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        for name in names:
            folder = out / name
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True)
            context = browser.new_context(
                record_har_path=str(folder / "recording.har"),
                record_har_content="embed",
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            recorder = Recorder(page, base, folder)
            try:
                FLOWS[name](recorder)
            finally:
                recorder.close()
                context.close()  # writes the HAR
            counts[name] = recorder.seq
            print(f"  {name}: {recorder.seq} steps -> {folder.relative_to(ROOT)}")
        browser.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="http://127.0.0.1:8730", help="where the sample runs")
    parser.add_argument("--out", type=Path, default=ROOT / "evidence" / "qtt")
    parser.add_argument("--flow", action="append", choices=sorted(FLOWS), help="default: all")
    parser.add_argument(
        "--reset",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="delete the sample's database and start the sample here (default); --no-reset "
        "records against a sample already running at --base",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args(argv)

    base = args.base.rstrip("/")
    proc = start_sample(base) if args.reset else None
    if proc is None and not healthy(base):
        sys.exit(f"no sample answering at {base}")
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DDL, args.out / "ddl.sql")
        print(f"recording QTT sample at {base} into {args.out}")
        record(base, args.out, args.flow or list(FLOWS), args.headed)
        print(f"  ddl.sql copied from {DDL.relative_to(ROOT)}")
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
