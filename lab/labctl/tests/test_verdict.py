"""Session verdict: changes an automation does not expect fail the session."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from labctl import repo, verdict
from labctl.models import ExpectedChange

ROOT = Path(__file__).resolve().parents[3]


def ch(table, op, key="1", columns=()):
    return {"app": "qtt", "table": table, "op": op, "key": key, "columns": list(columns)}


EXPECTS = [
    ExpectedChange(table="qc_reviews", ops=["update"], columns=["status", "decided_at"]),
    ExpectedChange(table="qc_findings", ops=["insert"], max_rows=2),
]


def test_expected_changes_pass():
    v = verdict.judge(
        [ch("qc_reviews", "update", columns=["status"]), ch("qc_findings", "insert")], EXPECTS
    )
    assert v.status == "PASS" and v.checked == 2 and not v.unexpected


def test_each_kind_of_unexpected_change_fails_with_its_reason():
    v = verdict.judge(
        [
            ch("loans", "update", columns=["amount"]),  # table not expected
            ch("qc_reviews", "delete"),  # op not expected
            ch("qc_reviews", "update", "2", ["status", "assignee"]),  # column not expected
            ch("QC_FINDINGS", "insert", "a"),  # case and schema do not matter
        ],
        EXPECTS,
    )
    assert v.status == "FAIL"
    why = {c["key"]: c["why"] for c in v.unexpected}
    assert set(why) == {"1", "2"} and len(v.unexpected) == 3
    assert any("table loans is not expected" in c["why"] for c in v.unexpected)
    assert any("delete is not expected on qc_reviews" in c["why"] for c in v.unexpected)
    assert "columns assignee of qc_reviews" in why["2"]


def test_too_many_rows_fails():
    many = [ch("qc_findings", "insert", str(n)) for n in range(3)]
    v = verdict.judge(many, EXPECTS)
    assert v.status == "FAIL" and v.over == ["qc_findings insert: 3 rows, at most 2 expected"]


def test_not_declared_and_declared_empty():
    assert verdict.judge([ch("x", "insert")], None).status == "NOT_DECLARED"
    assert verdict.judge([], []).status == "PASS"
    assert verdict.judge([ch("x", "insert")], []).status == "FAIL"


def test_learn_and_merge():
    learned = verdict.learn(
        [ch("t", "update", columns=["b"]), ch("t", "insert"), ch("u", "delete")]
    )
    assert [(e.table, e.ops, e.columns) for e in learned] == [
        ("t", ["insert", "update"], ["b"]),
        ("u", ["delete"], []),
    ]
    merged = verdict.merge(learned, verdict.learn([ch("t", "update", columns=["a"])]))
    assert merged[0].columns == ["a", "b"]
    open_any = verdict.merge(
        [ExpectedChange(table="t", ops=["update"])],
        [ExpectedChange(table="t", ops=["update"], columns=["a"])],
    )
    assert open_any[0].columns == []  # an update that allowed any column stays open
    first_update = verdict.merge(
        [ExpectedChange(table="t", ops=["insert"])],
        [ExpectedChange(table="t", ops=["update"], columns=["a"])],
    )
    assert first_update[0].ops == ["insert", "update"] and first_update[0].columns == ["a"]


def test_accept_writes_expects_and_the_file_stays_valid(tmp_path):
    src = ROOT / "automations" / "uipath-qtt-qc-review"
    dst = tmp_path / "automations" / "uipath-qtt-qc-review"
    shutil.copytree(src, dst)
    changes = [ch("qc_reviews", "update", columns=["status"])]
    expects = verdict.accept(tmp_path, "uipath-qtt-qc-review", changes)
    model, problems = repo.load(dst / "automation.yaml", "automation")
    assert not problems and model.expects == expects
    assert verdict.judge(changes, model.expects).status == "PASS"
    raw = yaml.safe_load((dst / "automation.yaml").read_text(encoding="utf-8"))
    assert raw["expects"] == [{"table": "qc_reviews", "ops": ["update"], "columns": ["status"]}]
    assert b"\r\n" not in (dst / "automation.yaml").read_bytes()


class FakeAgent:
    def __init__(self, session, detail=None):
        self._s, self._d = session, detail

    def session(self, _sid):
        return self._s

    def session_detail(self, _sid):
        return self._d


def test_older_sessions_take_columns_from_the_detail():
    session = {
        "automation": "a",
        "changes": [{"app": "qtt", "table": "t", "op": "update", "key": "1"}],
    }
    detail = {"changes": [{**session["changes"][0], "columns": ["c"], "before": {}, "after": {}}]}
    s, changes = verdict.session_changes(FakeAgent(session, detail), "ses_x")
    assert changes[0]["columns"] == ["c"] and "before" not in changes[0]
    assert s["changes"] == changes


@pytest.mark.parametrize("bad", [{"table": "t", "ops": []}, {"table": "t", "ops": ["upsert"]}])
def test_expected_change_contract(bad):
    with pytest.raises(ValueError):
        ExpectedChange.model_validate(bad)
