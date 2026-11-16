"""Value binding (I-11) and typed-versus-derived provenance (I-12) on a lab session."""

from __future__ import annotations

from labctl import provenance
from labctl.botlog.models import BotTrace


def trace():
    return BotTrace.model_validate({"run_id": "r1", "steps": [
        {"seq": 1, "action": "type", "field": "in_User", "selector": "input#username", "value": "demo.user"},
        {"seq": 2, "action": "type", "field": "in_Password", "selector": "input#password", "redacted": True},
        {"seq": 3, "action": "type", "field": "in_CaseNo", "selector": "input#caseNo", "value": "C-1013"},
        {"seq": 4, "action": "select", "field": "in_Category", "selector": "select#category", "value": "Payments"},
        {"seq": 5, "action": "select", "selector": "select#caseStatus", "value": "IN_PROGRESS"},
        {"seq": 6, "action": "type", "field": "in_Note", "selector": "#noteText", "value": "never stored"},
        {"seq": 7, "action": "read", "field": "out_Message", "selector": "#message", "value": "Case updated"},
    ]})  # fmt: skip


CHANGES = [
    {"table": "cases", "op": "update", "key": "13",
     "columns": ["status", "category_id", "updated_at"],
     "after": {"status": "IN_PROGRESS", "category_id": 2, "updated_at": "2026-11-16T10:00:00"}},
    {"table": "case_audit", "op": "insert", "key": "122",
     "after": {"id": 122, "case_id": 13, "actor": "demo.user", "event": "UPDATED"}},
    {"table": "cases", "op": "delete", "key": "99", "before": {"id": 99}},
]  # fmt: skip
ROWS = {
    "category": [{"id": 1, "name": "Cards"}, {"id": 2, "name": "Payments"}],
    "cases": [{"id": 13, "case_no": "C-1013"}],
}


def kinds(prov):
    return {(b.table, b.column): (b.kind, b.source) for b in prov.bindings}


def test_typed_selected_coded_and_derived():
    prov = provenance.bind(trace(), CHANGES, ROWS)
    k = kinds(prov)
    assert k[("cases", "status")] == ("selected", "select#caseStatus")
    assert k[("cases", "category_id")] == ("selected", "in_Category (stored as its code 2)")
    assert k[("case_audit", "case_id")] == ("typed", "in_CaseNo (stored as its code 13)")
    assert k[("case_audit", "actor")] == ("typed", "in_User")
    assert k[("cases", "updated_at")][0] == "derived"
    assert k[("case_audit", "event")][0] == "derived"
    assert ("cases", "id") not in k  # a delete writes nothing
    assert [e.field for e in prov.not_stored] == ["in_Note"]  # reads and redacted are not entered
    assert prov.counts() == {"typed": 2, "selected": 2, "derived": 3}


def test_without_lab_rows_codes_stay_derived_and_the_markdown_reads_plainly():
    prov = provenance.bind(trace(), CHANGES)
    assert kinds(prov)[("cases", "category_id")][0] == "derived"
    text = prov.markdown()
    assert "| `cases` | `13` | `status` | chosen in select#caseStatus |" in text
    assert "found in no written column" in text and "Payments" not in text  # names, not values


def test_combined_runs_keep_every_step():
    one = provenance.combined([trace(), trace()])
    assert [s.seq for s in one.steps] == list(range(1, 15))
