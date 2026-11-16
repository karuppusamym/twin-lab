"""WP-C: seeded synthetic rows, and the `labctl data` commands."""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from labctl.data import ddl, synthetic, workbook
from labctl.data.cli import app
from labctl.source.sqlalchemy_models import Column, Table

ROOT = Path(__file__).resolve().parents[3]
SHOWCASE = ROOT / "apps" / "showcase" / "db" / "schema.sql"
COUNTS = {"app_user": 3, "customer": 20, "category": 4, "subcategory": 10, "cases": 60,
          "case_note": 80, "case_audit": 40}  # fmt: skip
runner = CliRunner()


@pytest.fixture(scope="module")
def showcase():
    return ddl.parse(SHOWCASE.read_text(encoding="utf-8"))


def dumped(rows) -> str:
    return json.dumps(rows, sort_keys=False)


def test_same_seed_same_bytes_and_another_seed_differs(showcase):
    a = synthetic.generate(showcase, 42, COUNTS)
    b = synthetic.generate(showcase, 42, dict(reversed(COUNTS.items())))
    c = synthetic.generate(showcase, 43, COUNTS)
    assert dumped(a) == dumped(b)
    assert dumped(a) != dumped(c)
    assert {t: len(r) for t, r in a.items()} == COUNTS
    assert list(a) == ["app_user", "customer", "category", "subcategory", "cases", "case_note",
                       "case_audit"]  # fmt: skip


def test_generated_rows_respect_the_schema(showcase):
    rows = synthetic.generate(showcase, 1, COUNTS)
    by_name = {t.name: t for t in showcase}
    for name, table_rows in rows.items():
        table = by_name[name]
        for col in table.columns:
            values = [r.get(col.name) for r in table_rows]
            if col.autoincrement and not any(
                c.references == (name, col.name) for t in showcase for c in t.columns
            ):
                assert all(col.name not in r for r in table_rows), (name, col.name)
                continue
            if not col.nullable:
                assert None not in values, (name, col.name)
            m = re.fullmatch(r"VARCHAR\((\d+)\)", col.type)
            if m:
                assert all(v is None or len(v) <= int(m.group(1)) for v in values)
            if col.unique or col.primary_key:
                present = [v for v in values if v is not None]
                assert len(present) == len(set(present)), (name, col.name)
            if col.references:
                parent, pcol = col.references
                allowed = {r[pcol] for r in rows[parent]}
                assert all(v is None or v in allowed for v in values), (name, col.name)
    assert "id" not in rows["case_audit"][0]  # nothing refers to it: the database numbers it
    assert "id" in rows["cases"][0]


def test_no_real_looking_personal_data(showcase):
    rows = synthetic.generate(showcase, 5, COUNTS)
    for customer in rows["customer"]:
        assert customer["email"].endswith("@example.test")
        assert re.search(r"555[ -]01\d\d$", customer["phone"]), customer["phone"]
        assert re.fullmatch(r"C-\d{4}", customer["cust_no"])


def test_sibling_references_agree(showcase):
    rows = synthetic.generate(showcase, 9, COUNTS)
    parent_of = {s["id"]: s["category_id"] for s in rows["subcategory"]}
    for case in rows["cases"]:
        if case["subcategory_id"] is not None:
            assert case["category_id"] == parent_of[case["subcategory_id"]]


def test_later_moments_come_after_earlier_ones(showcase):
    rows = synthetic.generate(showcase, 3, COUNTS)
    assert all(c["updated_at"] >= c["opened_at"] for c in rows["cases"])


def test_generated_data_passes_validation(showcase):
    rows = synthetic.generate(showcase, 11, COUNTS)
    back, problems = workbook.read(workbook.template(showcase, rows), showcase)
    assert problems == []
    assert back == rows


def test_code_lists_come_from_samples(showcase):
    samples = {
        "cases": [{"status": s, "priority": p} for s, p in
                  [("NEW", "P1"), ("WAITING", "P2"), ("DONE", "P3"), ("NEW", None)]],
        "customer": [{"city": f"Town {i}"} for i in range(25)],  # too many to be a code list
    }  # fmt: skip
    rows = synthetic.generate(showcase, 2, COUNTS, samples)
    statuses = {c["status"] for c in rows["cases"]}
    assert statuses == {"NEW", "WAITING", "DONE"}
    assert {c["priority"] for c in rows["cases"]} <= {"P1", "P2", "P3"}
    assert not {c["city"] for c in rows["customer"]} & {f"Town {i}" for i in range(25)}


def test_foreign_keys_from_samples_for_parents_not_generated(showcase):
    samples = {"customer": [{"id": 101}, {"id": 102}], "category": [{"id": 7}],
               "subcategory": [{"id": 70, "category_id": 7}]}  # fmt: skip
    rows = synthetic.generate(showcase, 4, {"cases": 15}, samples)
    assert list(rows) == ["cases"]
    assert {c["customer_id"] for c in rows["cases"]} <= {101, 102}
    assert {c["subcategory_id"] for c in rows["cases"]} <= {70, None}


def test_clear_errors(showcase):
    with pytest.raises(ValueError, match="no table named nope"):
        synthetic.generate(showcase, 1, {"nope": 1})
    with pytest.raises(ValueError, match="cases.customer_id must refer to a customer row"):
        synthetic.generate(showcase, 1, {"cases": 1})
    flag = Table("flag", "flag", None, 1, [Column("on", "BOOLEAN", False, unique=True)])
    with pytest.raises(ValueError, match="cannot make 3 different values for flag.on"):
        synthetic.generate([flag], 1, {"flag": 3})


def test_types_and_composite_keys():
    team = Table("team", "team", None, 1, [
        Column("code", "VARCHAR(3)", False, primary_key=True),
        Column("budget", "NUMERIC(5,2)", True),
        Column("founded", "DATE", False),
        Column("seen", "TIMESTAMP", False),
        Column("ratio", "DOUBLE PRECISION", False),
        Column("ref", "UUID", False),
        Column("extra", "JSON", True),
        Column("logo", "BLOB", True),
        Column("starts", "TIME", False),
        Column("active", "BOOLEAN", False),
    ])  # fmt: skip
    link = Table("link", "link", None, 2, [
        Column("a", "VARCHAR(3)", False, primary_key=True, references=("team", "code")),
        Column("b", "VARCHAR(3)", False, primary_key=True, references=("team", "code")),
    ])  # fmt: skip
    rows = synthetic.generate([team, link], 8, {"team": 5, "link": 12})
    assert len({(r["a"], r["b"]) for r in rows["link"]}) == 12
    for r in rows["team"]:
        assert len(r["code"]) <= 3
        assert r["budget"] is None or re.fullmatch(r"\d{1,3}\.\d\d", r["budget"])
    back, problems = workbook.read(workbook.template([team, link], rows), [team, link])
    assert problems == [] and back == rows


# --- command line ------------------------------------------------------------------------------


def test_cli_template_generate_validate(tmp_path):
    book = tmp_path / "pack.xlsx"
    result = runner.invoke(app, ["generate", str(SHOWCASE), "--seed", "3", "--count",
                                 "customer=4", "--count", "app_user=1", "--out", str(book)])  # fmt: skip
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["validate", str(SHOWCASE), str(book)])
    assert result.exit_code == 0 and "OK: app_user 1, customer 4" in result.output

    as_json = tmp_path / "rows.json"
    result = runner.invoke(app, ["generate", str(SHOWCASE), "--seed", "3", "--count",
                                 "customer=4", "--count", "app_user=1", "--out", str(as_json)])  # fmt: skip
    assert result.exit_code == 0
    rows = json.loads(as_json.read_text(encoding="utf-8"))
    empty = tmp_path / "empty.xlsx"
    assert runner.invoke(app, ["template", str(SHOWCASE), "--out", str(empty)]).exit_code == 0
    filled = tmp_path / "filled.xlsx"
    result = runner.invoke(app, ["template", str(SHOWCASE), "--out", str(filled), "--rows",
                                 str(as_json)])  # fmt: skip
    assert result.exit_code == 0
    assert workbook.read(filled.read_bytes(), ddl.parse(SHOWCASE.read_text()))[0] == rows


def test_cli_validate_reports_problems(tmp_path):
    rows = {"cases": [{"id": 1, "case_no": "X" * 20, "customer_id": 5, "title": "t",
                       "status": "OPEN", "priority": "LOW", "opened_at": "2025-01-01T00:00:00",
                       "updated_at": "2025-01-01T00:00:00"}]}  # fmt: skip
    source = tmp_path / "rows.json"
    source.write_text(json.dumps({"rows": rows, "label": "bad"}), encoding="utf-8")
    book = tmp_path / "bad.xlsx"
    assert runner.invoke(app, ["template", str(SHOWCASE), "--out", str(book), "--rows",
                               str(source)]).exit_code == 0  # fmt: skip
    existing = tmp_path / "existing.json"
    existing.write_text(json.dumps({"customer": [{"id": 1}]}), encoding="utf-8")
    result = runner.invoke(app, ["validate", str(SHOWCASE), str(book), "--existing",
                                 str(existing)])  # fmt: skip
    assert result.exit_code == 1
    assert "Sheet 'cases', row 3, column B (case_no): is 20 characters long" in result.output
    assert "'5' not found in customer.id (the current customer data)" in result.output
    assert "2 problem(s)" in result.output


def test_cli_bad_arguments(tmp_path):
    result = runner.invoke(app, ["generate", str(SHOWCASE), "--seed", "1", "--count", "cases",
                                 "--out", str(tmp_path / "x.json")])  # fmt: skip
    assert result.exit_code == 2 and "use table=number" in result.output
    result = runner.invoke(app, ["generate", str(SHOWCASE), "--seed", "1", "--count", "cases=1",
                                 "--out", str(tmp_path / "x.json")])  # fmt: skip
    assert result.exit_code == 2 and "must refer to a customer row" in result.output
    result = runner.invoke(app, ["generate", str(SHOWCASE), "--seed", "1", "--count", "cases=1",
                                 "--out", str(tmp_path / "x.csv")])  # fmt: skip
    assert result.exit_code == 2 and ".xlsx or .json" in result.output
