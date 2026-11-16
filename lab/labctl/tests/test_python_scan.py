"""T-16: a Python bot's SQL tables and page selectors, read statically; undeclared ones are
reported as notes."""

from __future__ import annotations

import shutil
from pathlib import Path

from labctl import repo
from labctl.analyse import python as pyscan
from labctl.analyse import run as analysis

ROOT = Path(__file__).resolve().parents[3]

BOT = '''
import sqlite3
from playwright.sync_api import sync_playwright
from selenium.webdriver.common.by import By

OPEN = """
SELECT c.case_no, c.status, a.event FROM cases c JOIN case_audit a ON a.case_id = c.id
WHERE c.status = 'OPEN'
"""

def run(db, page, driver, table):
    db.execute(OPEN)
    db.execute(f"UPDATE cases SET status = ? WHERE case_no = ?", ("CLOSED", "C-1"))
    db.execute("INSERT INTO bot_runs (started) VALUES (?)", (1,))
    page.fill("#username", "x")
    page.locator("button#btnLogin").click()
    page.locator("#caseNo").press("Enter")
    page.wait_for_selector("table#results")
    status = page.text_content("td.status")
    driver.find_element(By.ID, "message")
    driver.find_element(By.CSS_SELECTOR, "ul#notes li")
    page.goto("/cases")
    return status
'''


def test_scan_finds_tables_and_selectors(tmp_path):
    f = tmp_path / "bot.py"
    f.write_text(BOT, encoding="utf-8")
    found = pyscan.scan([f])
    assert found.tables["cases"]["access"] == "read_write"
    assert set(found.tables["cases"]["columns"]) >= {"case_no", "status"}
    assert found.tables["case_audit"]["access"] == "read"
    assert found.tables["bot_runs"]["access"] == "write"
    sel = {s["selector"]: s["action"] for s in found.selectors}
    assert sel == {
        "#username": "type",
        "button#btnLogin": "click",
        "#caseNo": "read",  # .press("Enter") is a key, not a selector
        "table#results": "wait",
        "td.status": "read",
        "#message": "read",
        "ul#notes li": "read",
    }


def test_undeclared_table_and_selector_become_notes(tmp_path):
    src = ROOT / "automations" / "python-qtt-sla-report"
    dst = tmp_path / "automations" / "python-qtt-sla-report"
    shutil.copytree(src, dst)
    model, _ = repo.load(dst / "automation.yaml", "automation")
    before = analysis.analyse(dst, model)
    assert not [n for n in before.notes if "SQL uses table" in n.what]  # every table declared
    (dst / "extra.py").write_text(
        'def more(db, page):\n    db.execute("SELECT * FROM loan_holds")\n'
        '    page.click("#btnHold")\n',
        encoding="utf-8",
    )
    after = analysis.analyse(dst, model)
    notes = [n.what for n in after.notes]
    assert any("table loan_holds" in n and "no `touches`" in n for n in notes)
    assert any("selector #btnHold (click)" in n for n in notes)
    again = analysis.analyse(dst, model.model_validate(after.data))
    assert len([n for n in again.notes if "loan_holds" in n.what]) == 1  # not repeated
