"""WP-C: Excel templates and reading filled workbooks back, with every problem located."""

import io
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from labctl.data import ddl, workbook
from labctl.data.workbook import Problem
from labctl.source.sqlalchemy_models import Column, Table

ROOT = Path(__file__).resolve().parents[3]
SHOWCASE = ROOT / "apps" / "showcase" / "db" / "schema.sql"


@pytest.fixture(scope="module")
def showcase():
    return ddl.parse(SHOWCASE.read_text(encoding="utf-8"))


ROWS = {
    "app_user": [
        {"id": 1, "username": "demo", "password_hash": "0" * 64, "display_name": "Demo User"}
    ],  # fmt: skip
    "customer": [
        {
            "id": 1,
            "cust_no": "C-1001",
            "full_name": "Alex Ashdown",
            "email": "alex.ashdown@example.test",
            "phone": "555-0100",
            "city": "Springfield",
        },
        {
            "id": 2,
            "cust_no": "00123",
            "full_name": "Blake Birchwood",
            "email": "blake@example.test",
            "phone": "555-0101",
            "city": "=not a formula",
        },
    ],  # fmt: skip
    "category": [{"id": 1, "code": "BILL", "name": "Billing"}],
    "subcategory": [{"id": 1, "category_id": 1, "code": "BILL-REF", "name": "Refund"}],
    "cases": [
        {
            "id": 1,
            "case_no": "CS-1",
            "customer_id": 1,
            "title": "Question about invoice",
            "status": "OPEN",
            "priority": "HIGH",
            "category_id": 1,
            "subcategory_id": 1,
            "escalation_reason": None,
            "opened_at": "2025-03-01T09:30:00+00:00",
            "updated_at": "2025-03-02T10:00:00.250000+00:00",
        },
        {
            "id": 2,
            "case_no": "CS-2",
            "customer_id": 2,
            "title": "Refund",
            "status": "RESOLVED",
            "priority": "LOW",
            "category_id": None,
            "subcategory_id": None,
            "escalation_reason": "Needs a manager.",
            "opened_at": "2025-03-04T08:00:00+00:00",
            "updated_at": "2025-03-05T08:00:00.123456+00:00",
        },
    ],  # fmt: skip
    "case_note": [
        {
            "id": 1,
            "case_id": 1,
            "text": "Called back.",
            "author": "demo",
            "created_at": "2025-03-01T10:00:00+00:00",
        }
    ],  # fmt: skip
    "case_audit": [
        {
            "case_id": 2,
            "event": "RESOLVED",
            "detail": "Status changed",
            "actor": "demo",
            "created_ts": "2025-03-05T08:00:00+00:00",
        }
    ],  # fmt: skip
}


def test_template_layout(showcase):
    book = load_workbook(io.BytesIO(workbook.template(showcase)))
    assert book.sheetnames == ["README", "app_user", "customer", "category", "subcategory",
                               "cases", "case_note", "case_audit"]  # fmt: skip
    assert "How to fill" in book["README"]["A1"].value
    cases = book["cases"]
    header = [c.value for c in cases[1]]
    assert header == [c.name for c in next(t for t in showcase if t.name == "cases").columns]
    notes = {h: c.value for h, c in zip(header, cases[2], strict=True)}
    assert notes["id"].startswith("INTEGER") and "optional: numbered automatically" in notes["id"]
    assert "primary key" in notes["id"]
    assert notes["case_no"] == "VARCHAR(12) (text, at most 12 characters); required; unique"
    assert "refers to customer.id" in notes["customer_id"] and "required" in notes["customer_id"]
    assert "optional" in notes["category_id"]
    assert "UTC" in notes["opened_at"]
    assert cases["A2"].font.italic and cases["A2"].fill.fgColor.rgb.endswith("EDEDED")
    assert cases.freeze_panes == "A3"
    assert cases.max_row == 2


def test_round_trip_gives_identical_rows(showcase):
    data = workbook.template(showcase, ROWS)
    rows, problems = workbook.read(data, showcase)
    assert problems == []
    assert rows == ROWS


def test_formula_like_text_stays_text(showcase):
    book = load_workbook(io.BytesIO(workbook.template(showcase, ROWS)))
    cell = book["customer"]["F4"]
    assert cell.value == "=not a formula" and cell.data_type == "s"


def _book(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


SMALL = [
    Table("team", "team", None, 1, [
        Column("id", "INTEGER", False, primary_key=True, autoincrement=True),
        Column("code", "VARCHAR(5)", False, unique=True),
        Column("active", "BOOLEAN", True),
    ]),
    Table("person", "person", None, 2, [
        Column("id", "INTEGER", False, primary_key=True),
        Column("team_id", "INTEGER", False, references=("team", "id")),
        Column("born", "DATE", True),
        Column("seen", "TIMESTAMP", True),
        Column("pay", "NUMERIC(6,2)", True),
        Column("score", "DOUBLE PRECISION", True),
    ]),
]  # fmt: skip


def test_values_are_converted_by_type():
    data = _book({
        "team": [["id", "code", "active"], [1, "  A1  ", "yes"], [2, "B2", "No"], [3, "C3", 1],
                 [4, "D4", None]],
        "person": [["id", "team_id", "born", "seen", "pay", "score"],
                   [1, 1, date(1990, 5, 1), datetime(2025, 1, 2, 3, 4, 5), 12.5, 3],
                   ["2", "2", "1991-02-03", "2025-01-02 03:04:05", "7", "2.5"],
                   [3, 3, None, None, None, None]],
    })  # fmt: skip
    rows, problems = workbook.read(data, SMALL)
    assert problems == []
    assert rows["team"] == [
        {"id": 1, "code": "A1", "active": True},
        {"id": 2, "code": "B2", "active": False},
        {"id": 3, "code": "C3", "active": True},
        {"id": 4, "code": "D4", "active": None},
    ]
    assert rows["person"] == [
        {"id": 1, "team_id": 1, "born": "1990-05-01", "seen": "2025-01-02T03:04:05",
         "pay": "12.50", "score": 3.0},
        {"id": 2, "team_id": 2, "born": "1991-02-03", "seen": "2025-01-02T03:04:05",
         "pay": "7.00", "score": 2.5},
        {"id": 3, "team_id": 3, "born": None, "seen": None, "pay": None, "score": None},
    ]  # fmt: skip


def test_identity_left_empty_is_left_out_of_the_row():
    data = _book({"team": [["id", "code", "active"], [None, "A1", "true"]]})
    rows, problems = workbook.read(data, SMALL)
    assert problems == []
    assert rows == {"team": [{"code": "A1", "active": True}]}  # person sheet absent: not returned


def _where(problems: list[Problem]) -> list[tuple]:
    return [(p.sheet, p.row, p.column) for p in problems]


def test_every_problem_is_located():
    data = _book({
        "README": [["anything at all"]],
        "teams": [["id"]],
        "team": [["id", "code", "active", "colour"],
                 [1, "A1", "yes", "red"],
                 [2, "TOOLONG", "maybe", None],
                 [3, "A1", None, None],
                 [3, None, None, None]],
        "person": [["id", "born", "seen", "pay"],
                   [1, "05/01/1990", "soon", 12.345],
                   [1, datetime(1990, 1, 1, 12, 0), None, "12345"],
                   ["x", None, None, "1.234"]],
    })  # fmt: skip
    _, problems = workbook.read(data, SMALL)
    got = {(p.sheet, p.row, p.column): p.message for p in problems}
    expected = {
        ("teams", None, None): "there is no table with this name",
        ("team", 1, "D (colour)"): "there is no column colour in table team",
        ("team", 3, "B (code)"): "is 7 characters long; at most 5 allowed",
        ("team", 3, "C (active)"): "'maybe' is not yes or no",
        ("team", 4, "B (code)"): "duplicate value 'A1': must be unique, already used in row 2",
        ("team", 5, "B (code)"): "is required but empty",
        ("team", 5, "A (id)"): "duplicate primary key id=3: already used in row 4",
        ("person", 1, "team_id"): "required column is missing from row 1",
        ("person", 2, "B (born)"): "'05/01/1990' is not a date (use YYYY-MM-DD)",
        ("person", 2, "C (seen)"): "'soon' is not a date and time",
        ("person", 3, "B (born)"): "has a time of day; this column holds dates only",
        ("person", 3, "D (pay)"): "'12345' is too large for NUMERIC(6,2)",
        ("person", 3, "A (id)"): "duplicate primary key id=1: already used in row 2",
        ("person", 4, "A (id)"): "'x' is not a whole number",
        ("person", 4, "D (pay)"): "'1.234' has more than 2 decimal places",
    }
    for where, message in expected.items():
        assert where in got, (where, _where(problems))
        assert message in got[where], (where, got[where])
    assert len(problems) == len(expected), [str(p) for p in problems]
    assert ("person", 2, "D (pay)") not in got  # 12.345 from an Excel number is rounded


def test_problem_text_reads_well():
    p = Problem("customer", 5, "C (email)", "is required but empty")
    assert str(p) == "Sheet 'customer', row 5, column C (email): is required but empty"
    assert str(Problem("x", None, None, "no such table")) == "Sheet 'x': no such table"
    assert str(Problem("", None, None, "not a workbook")) == "not a workbook"


def test_not_a_workbook():
    rows, problems = workbook.read(b"this is not a zip file", SMALL)
    assert rows == {}
    assert len(problems) == 1 and "not an Excel workbook" in problems[0].message


def test_foreign_keys_checked_in_the_workbook():
    data = _book({
        "team": [["id", "code"], [1, "A1"]],
        "person": [["id", "team_id"], [1, 1], [2, 9]],
    })  # fmt: skip
    _, problems = workbook.read(data, SMALL)
    assert _where(problems) == [("person", 3, "B (team_id)")]
    assert problems[0].message == "'9' not found in team.id (sheet team)"


def test_foreign_keys_checked_against_existing_data():
    data = _book({"person": [["id", "team_id"], [1, 1], [2, 2], [3, "7"]]})
    existing = {"team": [{"id": 1, "code": "A1"}, {"id": "7", "code": "B1"}]}
    _, problems = workbook.read(data, SMALL, existing)
    assert _where(problems) == [("person", 3, "B (team_id)")]
    assert "'2' not found in team.id (the current team data)" in problems[0].message


def test_foreign_keys_with_nothing_to_check_against():
    data = _book({"person": [["id", "team_id"], [1, 1], [2, 2]]})
    _, problems = workbook.read(data, SMALL)
    assert _where(problems) == [("person", 2, "B (team_id)")]
    assert "no team sheet" in problems[0].message and "2 row(s)" in problems[0].message


def test_workbook_sheet_rows_override_existing_for_the_same_table():
    data = _book({"team": [["id", "code"], [5, "E5"]], "person": [["id", "team_id"], [1, 1]]})
    existing = {"team": [{"id": 1, "code": "A1"}]}  # replaced by the team sheet when loaded
    _, problems = workbook.read(data, SMALL, existing)
    assert _where(problems) == [("person", 2, "B (team_id)")]


def test_notes_row_may_be_deleted_and_blank_rows_are_skipped():
    data = _book({"team": [["id", "code"], [], [1, "A1"], [None, None], [2, "B2"]]})
    rows, problems = workbook.read(data, SMALL)
    assert problems == []
    assert [r["code"] for r in rows["team"]] == ["A1", "B2"]


def test_long_table_names_get_valid_sheet_names():
    long = Table("a_really_long_table_name_for_testing_sheets", "x", None, 1,
                 [Column("id", "INTEGER", False, primary_key=True)])  # fmt: skip
    twin = Table("a_really_long_table_name_for_testing_sheets_too", "y", None, 2,
                 [Column("id", "INTEGER", False, primary_key=True)])  # fmt: skip
    names = workbook.sheet_names([long, twin])
    assert all(len(n) <= 31 for n in names.values())
    assert len(set(names.values())) == 2
    data = workbook.template([long, twin], {long.name: [{"id": 1}], twin.name: [{"id": 2}]})
    rows, problems = workbook.read(data, [long, twin])
    assert problems == [] and rows == {long.name: [{"id": 1}], twin.name: [{"id": 2}]}


def test_an_empty_sheet_counts_as_left_out(showcase):
    rows, problems = workbook.read(workbook.template(showcase), showcase)
    assert rows == {} and problems == []
    existing = {"customer": [{"id": 1}]}
    data = workbook.template(showcase, {"cases": ROWS["cases"][:1]})
    _, problems = workbook.read(data, showcase, {**existing, "category": [{"id": 1}],
                                                 "subcategory": [{"id": 1}]})  # fmt: skip
    assert problems == []
