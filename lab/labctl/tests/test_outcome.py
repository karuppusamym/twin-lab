"""`outcome:` in automation.yaml: business success per automation, judged apart from `expects:`."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from labctl import e2e, harness, outcome, repo, verdict
from labctl.models import Automation, ExpectedChange, OutcomeCase

ROOT = Path(__file__).resolve().parents[3]


def case(kind="changes", **kw):
    if kind == "changes" and "requires" not in kw:
        kw["requires"] = [
            {
                "table": "cases",
                "op": "update",
                "columns": ["status"],
                "set": {"status": "IN_PROGRESS"},
            }
        ]
    return OutcomeCase.model_validate({"name": kw.pop("name", "main"), "kind": kind, **kw})


def upd(status="IN_PROGRESS", key="C-1", **extra):
    c = {"app": "showcase", "table": "cases", "op": "update", "key": key, "columns": ["status"]}
    if status is not None:
        c["after"] = {"status": status}
    return {**c, **extra}


# --- model ----------------------------------------------------------------------------------


def test_model_validation():
    with pytest.raises(ValidationError):
        OutcomeCase(name="x", kind="changes")  # needs requires
    with pytest.raises(ValidationError):
        OutcomeCase.model_validate(
            {"name": "x", "kind": "read_only", "requires": [{"table": "t", "op": "insert"}]}
        )
    with pytest.raises(ValidationError):
        case(requires=[{"table": "t", "op": "insert", "columns": ["a"]}])
    with pytest.raises(ValidationError):
        case(requires=[{"table": "t", "op": "insert", "min_rows": 3, "max_rows": 2}])
    with pytest.raises(ValidationError):
        case(requires=[{"table": "t", "op": "bogus"}])
    ok = case(requires=[{"table": "t", "op": "update", "set": {"n": 5}}])
    assert ok.requires[0].set == {"n": "5"}


def test_automation_outcome_names_unique_and_optional():
    base = repo.load(ROOT / "specs" / "automation.example.yaml", "automation")[0]
    data = base.model_dump(mode="json")
    assert Automation.model_validate(data).outcome is None
    c = {"name": "a", "kind": "read_only"}
    data["outcome"] = [c, c]
    with pytest.raises(ValidationError):
        Automation.model_validate(data)


def test_reference_automation_declares_a_truthful_case():
    path = ROOT / "automations/uipath-case-update/automation.yaml"
    model, problems = repo.load(path, "automation")
    assert not problems and model.outcome
    c = outcome.select_case(model.outcome)
    assert c.kind == "changes" and c.requires[0].set == {"status": "IN_PROGRESS"}
    # what the case requires is allowed by `expects`
    assert verdict.judge([upd()], model.expects).status == verdict.PASS
    assert outcome.judge([upd()], {}, c).status == outcome.MET


# --- kinds ----------------------------------------------------------------------------------


def test_changes_met_and_not_met():
    assert outcome.judge([upd()], {}, case()).status == outcome.MET
    o = outcome.judge([], {}, case())
    assert o.status == outcome.NOT_MET
    assert o.unmet == [
        "expected at least 1 update of cases touching status setting status=IN_PROGRESS; saw 0"
    ]
    wrong = outcome.judge([upd("RESOLVED")], {}, case())
    assert wrong.status == outcome.NOT_MET and "saw 0" in wrong.unmet[0]
    assert "other values" in wrong.unmet[0]


def test_changes_row_counts_and_columns():
    c = case(requires=[{"table": "case_audit", "op": "insert", "min_rows": 2, "max_rows": 3}])
    ins = {"table": "case_audit", "op": "insert", "key": "1"}
    assert outcome.judge([ins], {}, c).status == outcome.NOT_MET
    assert outcome.judge([ins, ins], {}, c).status == outcome.MET
    assert outcome.judge([ins] * 4, {}, c).status == outcome.NOT_MET
    cols = case(requires=[{"table": "cases", "op": "update", "columns": ["status", "category_id"]}])
    o = outcome.judge([upd()], {}, cols)
    assert o.status == outcome.NOT_MET and "category_id" in o.unmet[0]


def test_table_names_match_loosely():
    c = case(requires=[{"table": "dbo.Cases", "op": "update"}])
    assert outcome.judge([upd()], {}, c).status == outcome.MET


def test_cannot_check_when_values_absent():
    o = outcome.judge([upd(status=None)], {}, case())
    assert o.status == outcome.CANNOT_CHECK and "cannot check values" in o.unchecked[0]
    # a definite miss still wins over an unchecked part
    two = case(
        requires=[
            {"table": "cases", "op": "update", "set": {"status": "X"}},
            {"table": "case_audit", "op": "insert"},
        ]
    )
    assert outcome.judge([upd(status=None)], {}, two).status == outcome.NOT_MET


def test_cannot_check_when_update_columns_not_recorded():
    c = case(requires=[{"table": "cases", "op": "update", "columns": ["status"]}])
    bare = {"table": "cases", "op": "update", "key": "1"}
    assert outcome.judge([bare], {}, c).status == outcome.CANNOT_CHECK


def test_read_only():
    c = case("read_only")
    assert outcome.judge([], {}, c).status == outcome.MET
    o = outcome.judge([upd()], {}, c)
    assert o.status == outcome.NOT_MET and "expected no row changes; saw 1" in o.unmet[0]


@pytest.mark.parametrize("kind", ["rejected", "failed_run"])
def test_rejected_and_failed_run(kind):
    c = case(kind)
    assert outcome.judge([], {}, c, run_ok=False).status == outcome.MET
    assert outcome.judge([], {}, c, run_ok=True).status == outcome.NOT_MET
    assert outcome.judge([], {}, c).status == outcome.CANNOT_CHECK  # run result unknown
    o = outcome.judge([upd()], {}, c, run_ok=False)
    assert o.status == outcome.NOT_MET and "unchanged" in o.unmet[0]


def test_not_declared():
    o = outcome.judge([upd()], {}, None)
    assert o.status == outcome.NOT_DECLARED and o.summary() == "no outcome declared"
    assert o.to_dict()["status"] == "NOT_DECLARED"


# --- case selection ---------------------------------------------------------------------------


def test_case_selection():
    a = case("read_only", name="a")
    b = case("read_only", name="b")
    d = case("read_only", name="default")
    assert outcome.select_case([a, b]).name == "a"
    assert outcome.select_case([a, b, d]).name == "default"
    assert outcome.select_case([a, b], "b").name == "b"
    assert outcome.select_case(None) is None
    with pytest.raises(ValueError, match="cases: a, b"):
        outcome.select_case([a, b], "zzz")
    with pytest.raises(ValueError):
        outcome.select_case(None, "a")


def test_with_values_merges_after_from_detail():
    doc = [{"app": "s", "table": "cases", "op": "update", "key": "C-1", "columns": []}]
    detail = [
        {
            "table": "cases",
            "op": "update",
            "key": "C-1",
            "columns": ["status"],
            "after": {"status": "IN_PROGRESS"},
        }
    ]
    merged = outcome.with_values(doc, detail)
    assert merged[0]["after"] == {"status": "IN_PROGRESS"} and merged[0]["columns"] == ["status"]
    assert "after" not in outcome.with_values(doc, None)[0]


# --- the verdict is untouched ----------------------------------------------------------------


def test_verdict_unchanged_by_outcome():
    expects = [ExpectedChange(table="cases", ops=["update"], columns=["status"])]
    changes = [upd("RESOLVED")]
    before = verdict.judge(changes, expects).to_dict()
    outcome.judge(changes, {}, case())
    assert verdict.judge(changes, expects).to_dict() == before
    assert before["status"] == verdict.PASS
    # a zero-change session still passes the verdict while the outcome is not met
    assert verdict.judge([], expects).status == verdict.PASS
    assert outcome.judge([], {}, case()).status == outcome.NOT_MET


# --- from a stored session (fake agent) --------------------------------------------------------


class FakeAgent:
    def __init__(self, changes, detail):
        self._c, self._d = changes, detail

    def session(self, sid):
        return {"session_id": sid, "automation": "uipath-case-update", "changes": self._c}

    def session_detail(self, sid):
        return {"changes": self._d}


def test_for_session_reads_values_from_the_detail(monkeypatch):
    from labctl import lab as lab_mod

    doc = [
        {"app": "showcase", "table": "cases", "op": "update", "key": "C-1", "columns": ["status"]}
    ]
    detail = [
        {
            "table": "cases",
            "op": "update",
            "key": "C-1",
            "columns": ["status"],
            "after": {"status": "IN_PROGRESS"},
        }
    ]
    monkeypatch.setattr(lab_mod, "agent", lambda root, app: FakeAgent(doc, detail))
    o, auto = outcome.for_session(ROOT, "showcase", "ses_1")
    assert auto == "uipath-case-update" and o.status == outcome.MET
    assert o.case == "set-in-progress"
    monkeypatch.setattr(lab_mod, "agent", lambda root, app: FakeAgent(doc, []))
    assert outcome.for_session(ROOT, "showcase", "ses_1")[0].status == outcome.CANNOT_CHECK
    with pytest.raises(ValueError):
        outcome.for_session(ROOT, "showcase", "ses_1", case_name="nope")


# --- reports ----------------------------------------------------------------------------------


def chain(outcome_dict, status="PASS"):
    r = harness.ChainResult(status, "bot", "app", Path("."), session_id="ses_1")
    r.runs = [{"job_id": "abcdef123", "state": "Successful", "inputs": {}}]
    r.verdict = {"status": "PASS", "reasons": [], "unexpected": []}
    r.outcome = outcome_dict
    return r


def test_harness_report_wording():
    met = outcome.judge([upd()], {}, case()).to_dict()
    text = harness.render(chain(met), {}, None)
    assert "**Verdict on row changes: PASS**" in text
    assert "**Business outcome: MET (case main, changes)**" in text
    bad = outcome.judge([], {}, case()).to_dict()
    text = harness.render(chain(bad), {}, None)
    assert "Business outcome: NOT MET" in text and "- expected at least 1 update of cases" in text
    none = outcome.judge([], {}, None).to_dict()
    assert "**Business outcome: no outcome declared**" in harness.render(chain(none), {}, None)
    assert "Business outcome" not in harness.render(chain(None), {}, None)


def test_e2e_detail_wording():
    assert e2e.outcome_detail(None) == "no outcome declared"
    assert e2e.outcome_detail(outcome.judge([], {}, None).to_dict()) == "no outcome declared"
    met = outcome.judge([upd()], {}, case()).to_dict()
    assert e2e.outcome_detail(met) == "outcome MET (case main)"
