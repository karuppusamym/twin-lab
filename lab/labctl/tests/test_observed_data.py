"""Observed profiles: what a recording may leave in a profile, and generation that follows one.

All fixtures are synthetic. The "recording" is a hand-made HAR whose values play the part of real
ones: names, e-mail addresses, telephone numbers and long numbers that must never reach a profile.
"""

import json
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from labctl.data import ddl, observed, synthetic
from labctl.data.cli import app

runner = CliRunner()

SCHEMA = """
CREATE TABLE customer (
  customer_id INTEGER PRIMARY KEY,
  full_name VARCHAR(60) NOT NULL,
  email VARCHAR(80)
);
CREATE TABLE cases (
  case_id INTEGER PRIMARY KEY,
  case_no VARCHAR(12) NOT NULL UNIQUE,
  customer_id INTEGER NOT NULL REFERENCES customer(customer_id),
  status VARCHAR(20) NOT NULL,
  category VARCHAR(20),
  amount NUMERIC(10,2),
  opened_on DATE,
  notes TEXT
);
"""
NAMES = ["Jane Doe", "Rosa Marlowe", "Tomas Wickham", "Ines Carvalho", "Olu Bankole"]
STATUSES = ["OPEN", "OPEN", "OPEN", "RESOLVED", "RESOLVED", "ESCALATED"]
SECRETS = ["jane.doe@bank.example", "+1 415 555 0132", "Rosa Marlowe", "4929123412341234",
           "12 Elm Street", "Customer rang about her mortgage"]  # fmt: skip


def _case(i: int, status: str) -> dict:
    return {
        "caseNo": f"C-{1001 + i}", "status": status, "category": "Billing" if i % 2 else "Access",
        "customerName": NAMES[i % 5], "email": f"{NAMES[i % 5].split()[0].lower()}@bank.example",
        "phone": f"+1 415 555 01{i:02d}", "accountNumber": f"49291234{i:08d}",
        "amount": 100.5 + i * 40, "openedOn": f"2026-0{1 + i % 6}-1{i % 9}",
        "notes": "Customer rang about her mortgage and was upset " + NAMES[i % 5],
    }  # fmt: skip


def _entry(method: str, url: str, body, ts: int, status: int = 200, mime: str = "application/json"):
    text = body if isinstance(body, str) else json.dumps(body)
    return {
        "startedDateTime": f"2026-11-01T10:00:{ts:02d}.000Z", "time": 20,
        "_resourceType": "xhr" if "json" in mime else "document",
        "request": {"method": method, "url": url, "headers": []},
        "response": {"status": status, "headers": [],
                     "content": {"mimeType": mime, "text": text}},
    }  # fmt: skip


def _har() -> dict:
    rows = [_case(i, s) for i, s in enumerate(STATUSES)]
    start = dict(rows[0], status="OPEN")
    changed = dict(rows[0], status="RESOLVED")
    entries = [
        _entry("GET", "http://app.test/api/cases", rows, 1),
        _entry("GET", "http://app.test/api/cases/C-1001", start, 2),
        _entry("POST", "http://app.test/api/cases/C-1001/resolve", {"ok": True}, 3),
        _entry("GET", "http://app.test/api/cases/C-1001", changed, 4),
        _entry("GET", "http://app.test/api/cases/C-1003", dict(rows[2], status="OPEN"), 5),
        _entry("POST", "http://app.test/api/cases/C-1003/resolve", {"ok": True}, 6),
        _entry("GET", "http://app.test/api/cases/C-1004", dict(rows[3]), 7),
    ]
    return {"log": {"version": "1.2", "entries": entries}}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    rec = tmp_path / "evidence" / "demo" / "flow-1"
    rec.mkdir(parents=True)
    (rec / "recording.har").write_text(json.dumps(_har()), encoding="utf-8")
    db = tmp_path / "apps" / "demo" / "db"
    db.mkdir(parents=True)
    (db / "schema.sql").write_text(SCHEMA, encoding="utf-8")
    return tmp_path


@pytest.fixture
def tables():
    return ddl.parse(SCHEMA)


@pytest.fixture
def profile(root, tables):
    return observed.build(root, "demo", tables)


# --- the classifier ---------------------------------------------------------------------------


def test_repeated_short_vocabulary_is_kept_with_shares():
    info = observed.classify_column("status", ["OPEN"] * 3 + ["RESOLVED"] * 2 + ["ESCALATED"])
    assert info["kind"] == "code"
    assert info["values"] == {"OPEN": 0.5, "RESOLVED": 0.33, "ESCALATED": 0.17}


@pytest.mark.parametrize("column,values", [
    ("email", ["jane@bank.example", "rosa@bank.example"] * 2),
    ("contact", ["jane@bank.example"] * 3),  # an e-mail under an innocent name
    ("phone", ["+1 415 555 0132", "+1 415 555 0177"] * 2),
    ("mobile_no", ["+44 7700 900123"] * 3),
    ("customer_name", ["Jane Doe", "Rosa Marlowe"] * 3),
    ("lender", ["Jane Doe", "Jane Doe", "Rosa Marlowe", "Rosa Marlowe"]),  # repeated names
    ("who", ["Jane", "Jane", "Rosa", "Rosa"]),  # repeated first names, no code-like column name
    ("account_number", ["4929123412341234"] * 4),
    ("reference", [f"R{n}" for n in range(1, 40)]),  # all different: identifiers
    ("note", ["Customer rang about the invoice"] * 4),
    ("address", ["12 Elm Street", "7 Oak Street"] * 2),
    ("status", ["4929123412341234", "4929123412341234"]),  # digits-heavy under a code name
    ("status", ["x" * 31, "x" * 31]),  # too long
    ("status", ["Xxxx", "Xxxx"]),  # a masked value is not a word
])  # fmt: skip
def test_personal_and_long_values_are_never_kept(column, values):
    info = observed.classify_column(column, values)
    assert "values" not in info
    assert info["kind"] != "code"
    assert not any(v in json.dumps(info) for v in set(values) if len(v) > 3)


def test_a_single_title_case_observation_is_not_a_code_but_an_upper_one_is():
    assert "values" not in observed.classify_column("status", ["Rosa"])
    assert observed.classify_column("status", ["OPEN"])["values"] == {"OPEN": 1.0}


def test_a_vocabulary_value_that_is_also_a_personal_value_is_dropped():
    personal = frozenset({"avery"})
    info = observed.classify_column("category", ["Avery", "Avery", "Credit", "Credit"], personal)
    assert "values" not in info


def test_shapes_only_for_identifiers_numbers_and_dates():
    ident = observed.classify_column("case_no", [f"C-{n}" for n in range(1001, 1030)])
    assert ident["kind"] == "identifier" and ident["pattern"] == "[C]-####"
    assert "values" not in ident
    loan = observed.classify_column("loan_no", [f"L-{n}" for n in range(200001, 200030)])
    assert loan["pattern"] == "######"  # no literal prefix for an account-like column
    num = observed.classify_column("amount", [100.5, 240.25, 999.0])
    assert num["kind"] == "number" and num["range"][0] <= 100.5 and num["range"][1] >= 999
    money = observed.classify_column("loan_amount", ["$636,500.00", "$82,000.00"])
    assert money["kind"] == "number" and money["format"] == "currency"
    dates = observed.classify_column("opened_on", ["2026-01-12", "2026-04-02"])
    assert dates["range"] == ["2026-01", "2026-04"]
    born = observed.classify_column("birth_date", ["1980-02-03", "1975-05-06"])
    assert "range" not in born


def test_pattern_round_trip():
    assert observed.pattern_of(["R-1001", "R-1007"]) == "[R]-####"
    assert observed.pattern_of(["R-1001", "R-1007"], literal_prefix=False) == "####"
    assert observed.pattern_of(["Jane Doe"]) is None
    assert observed.make_from_pattern("[R]-####", 7) == "R-0007"
    assert observed.keep_anchor_id("case_no", "cases", "C-1001")
    assert not observed.keep_anchor_id("loan_no", "loans", "L-200001")
    assert not observed.keep_anchor_id("account_no", "cases", "A-1001")
    assert not observed.keep_anchor_id("customer_no", "cases", "C-1001")
    assert not observed.keep_anchor_id("case_no", "customers", "C-1001")
    assert not observed.keep_anchor_id("case_no", "cases", "4929123412341234")


# --- building a profile from evidence ----------------------------------------------------------------


def test_profile_has_codes_shapes_rows_and_nothing_personal(root, profile):
    cols = profile["tables"]["cases"]["columns"]
    assert cols["status"]["kind"] == "code"
    assert set(cols["status"]["values"]) == {"OPEN", "RESOLVED", "ESCALATED"}
    assert cols["status"]["values"]["OPEN"] > cols["status"]["values"]["ESCALATED"]
    assert cols["category"]["values"].keys() == {"Billing", "Access"}
    assert cols["case_no"]["pattern"] == "[C]-####"
    assert cols["amount"]["kind"] == "number"
    assert cols["opened_on"]["kind"] == "date"
    assert cols["customer_name"]["kind"] == "personal"
    assert cols["email"]["kind"] == "email"
    assert profile["tables"]["cases"]["rows_seen"] == 6
    out = root / "apps" / "demo" / "db" / "observed-profile.yaml"
    observed.write(profile, out)
    text = out.read_text(encoding="utf-8")
    for value in NAMES + SECRETS + ["bank.example", "49291234", "mortgage", "555 01"]:
        assert value not in text, value
    assert "Jane" not in text and "upset" not in text


def test_anchors_keep_case_numbers_and_the_state_before_the_change(profile):
    anchors = {a["key"]["id"]: a for a in profile["anchors"]}
    assert set(anchors) == {"C-1001", "C-1003", "C-1004"}
    first = anchors["C-1001"]
    assert first["table"] == "cases" and first["key"]["column"] == "case_no"
    assert first["state"]["status"] == "OPEN"  # not the RESOLVED seen after the change
    assert first["state_seen_before_change"] is True
    assert anchors["C-1004"]["state"]["status"] == "RESOLVED"
    assert "customer_name" not in first["state"] and "notes" not in first["state"]


def test_a_state_seen_only_after_a_change_is_not_recorded(root, tables):
    har = _har()
    entries = har["log"]["entries"]
    har["log"]["entries"] = [entries[2], entries[3]]  # the change, then a look at the result
    (root / "evidence" / "demo" / "flow-1" / "recording.har").write_text(json.dumps(har))
    profile = observed.build(root, "demo", tables)
    assert profile["anchors"][0]["state"] == {}
    assert profile["anchors"][0]["state_seen_before_change"] is False


def test_html_tables_and_record_pages_feed_the_profile(tmp_path, tables):
    rec = tmp_path / "evidence" / "demo" / "r"
    rec.mkdir(parents=True)
    rows = "".join(
        f"<tr><td>C-{1001 + i}</td><td>{s}</td><td>{NAMES[i % 5]}</td></tr>"
        for i, s in enumerate(STATUSES)
    )
    queue = ("<html><body><table id='q'><thead><tr><th>Case no</th><th>Status</th>"
             f"<th>Customer name</th></tr></thead><tbody>{rows}</tbody></table></body></html>")  # fmt: skip
    page = (
        "<html><body><h1>Case <span id='caseNo'>C-1001</span></h1>"
        "<span id='caseStatus'>OPEN</span></body></html>"
    )
    har = {"log": {"entries": [
        _entry("GET", "http://app.test/cases", queue, 1, mime="text/html"),
        _entry("GET", "http://app.test/cases/C-1001", page, 2, mime="text/html"),
        _entry("POST", "http://app.test/api/cases/C-1001/resolve", {"ok": 1}, 3),
    ]}}  # fmt: skip
    (rec / "recording.har").write_text(json.dumps(har))
    profile = observed.build(tmp_path, "demo", tables)
    cols = profile["tables"]["cases"]["columns"]
    assert cols["status"]["values"].keys() == {"OPEN", "RESOLVED", "ESCALATED"}
    assert cols["customer_name"]["kind"] == "personal"
    assert profile["tables"]["cases"]["rows_seen"] == 6
    assert profile["anchors"][0]["key"] == {"column": "case_no", "shape": "[C]-####",
                                            "verbatim": True, "id": "C-1001"}  # fmt: skip
    assert profile["anchors"][0]["state"] == {"status": "OPEN"}
    assert "Jane Doe" not in yaml.safe_dump(profile)


def test_a_profile_with_a_value_that_may_not_be_kept_is_refused(profile):
    bad = json.loads(json.dumps(profile))
    bad["tables"]["cases"]["columns"]["status"]["values"]["jane@bank.example"] = 0.1
    with pytest.raises(observed.ProfileError):
        observed.Profile(bad)
    bad = json.loads(json.dumps(profile))
    bad["tables"]["cases"]["columns"]["case_no"]["values"] = {"C-1001": 1.0}
    with pytest.raises(observed.ProfileError):
        observed.check(bad)
    bad = json.loads(json.dumps(profile))
    bad["anchors"][0]["key"]["id"] = "4929123412341234"
    with pytest.raises(observed.ProfileError):
        observed.check(bad)


# --- generation ------------------------------------------------------------------------------------------


def _counts():
    return {"customer": 5, "cases": 40}


def test_without_a_profile_nothing_changes(tables):
    plain = synthetic.generate(tables, 7, _counts())
    assert synthetic.generate(tables, 7, _counts(), None, None, None) == plain
    other = {"tables": {"unrelated": {"columns": {"x": {"kind": "code", "values": {"A": 1.0}}}}}}
    assert synthetic.generate(tables, 7, _counts(), profile=other) == plain


def test_generated_rows_follow_the_profile(tables, profile):
    rows = synthetic.generate(tables, 7, _counts(), profile=profile)
    cases = rows["cases"]
    assert len(cases) == 40
    assert {r["status"] for r in cases} <= {"OPEN", "RESOLVED", "ESCALATED"}
    assert {r["category"] for r in cases if r["category"]} <= {"Billing", "Access"}
    share = sum(r["status"] == "OPEN" for r in cases) / len(cases)
    assert share > sum(r["status"] == "ESCALATED" for r in cases) / len(cases)
    assert all(re.fullmatch(r"C-\d{4}", r["case_no"]) for r in cases)
    assert len({r["case_no"] for r in cases}) == 40
    amounts = [float(r["amount"]) for r in cases if r["amount"] is not None]
    lo, hi = profile["tables"]["cases"]["columns"]["amount"]["range"]
    assert amounts and all(lo <= a <= hi for a in amounts)
    assert all("2026-01" <= r["opened_on"][:7] <= "2026-06" for r in cases if r["opened_on"])
    names = {r["full_name"] for r in rows["customer"]}
    assert not names & set(NAMES)  # generated, never copied
    assert synthetic.generate(tables, 7, _counts(), profile=profile) == rows  # reproducible


def test_anchor_rows_exist_with_their_starting_state(tables, profile):
    report: dict = {}
    rows = synthetic.generate(
        tables, 3, {"customer": 5, "cases": 2}, profile=profile, report=report
    )
    by_no = {r["case_no"]: r for r in rows["cases"]}
    assert len(rows["cases"]) == 3  # the count was raised to hold the anchors
    assert by_no["C-1001"]["status"] == "OPEN"
    assert by_no["C-1004"]["status"] == "RESOLVED"
    assert {a["identifier"] for a in report["anchors"]} == {"C-1001", "C-1003", "C-1004"}
    assert all(a["identifier_kept_from_recording"] for a in report["anchors"])


def test_anchor_with_a_private_shape_gets_a_stand_in_and_a_report_line(tables, profile):
    data = json.loads(json.dumps(profile))
    anchor = data["anchors"][0]
    anchor["key"] = {"column": "case_no", "shape": "######", "verbatim": False}
    report: dict = {}
    rows = synthetic.generate(tables, 3, {"customer": 5, "cases": 5}, profile=data, report=report)
    first = report["anchors"][0]
    assert first["identifier_kept_from_recording"] is False
    assert first["identifier"].startswith("TEST") and first["stand_in_for_shape"] == "######"
    assert first["identifier"] in {r["case_no"] for r in rows["cases"]}


# --- command line ---------------------------------------------------------------------------------------------


def test_observe_and_generate_from_observed(root):
    schema = root / "apps" / "demo" / "db" / "schema.sql"
    res = runner.invoke(app, ["observe", "demo", "--root", str(root)])
    assert res.exit_code == 0, res.output
    assert "kept verbatim" in res.output and "shape only" in res.output
    prof = root / "apps" / "demo" / "db" / "observed-profile.yaml"
    assert prof.is_file()
    out = root / "rows.json"
    res = runner.invoke(app, ["generate", str(schema), "--seed", "1", "--from-observed",
                              "--count", "customer=5", "--out", str(out)])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "anchor cases.case_no" in res.output
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert {"C-1001", "C-1003", "C-1004"} <= {r["case_no"] for r in rows["cases"]}
    res = runner.invoke(app, ["generate", str(schema), "--seed", "1", "--profile", str(prof),
                              "--count", "customer=2", "--count", "cases=0", "--out", str(out)])  # fmt: skip
    assert res.exit_code == 0, res.output


def test_generate_without_counts_or_profile_is_refused(root):
    schema = root / "apps" / "demo" / "db" / "schema.sql"
    res = runner.invoke(
        app, ["generate", str(schema), "--seed", "1", "--out", str(root / "o.json")]
    )
    assert res.exit_code == 2
