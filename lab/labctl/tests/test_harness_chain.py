"""`labctl harness run --repeat`: runs agree only when each passed and changed the same rows."""

from __future__ import annotations

from pathlib import Path

from labctl import harness


def result(status="PASS", kinds=("cases: update (status)",), sid="ses_1"):
    r = harness.ChainResult(status, "bot", "app", Path("."), session_id=sid, wall_s=1.0)
    r.change_kinds = list(kinds)
    return r


def test_consistent_runs_pass():
    status, text = harness.consistency([result(sid="a"), result(sid="b")])
    assert status == "PASS" and "| 2 | PASS |" in text


def test_different_changes_or_a_failed_run_fail():
    status, text = harness.consistency([result(), result(kinds=("cases: update (status)",
                                                               "case_audit: insert"))])  # fmt: skip
    assert status == "FAIL" and "not consistent" in text
    assert harness.consistency([result(), result(status="FAIL")])[0] == "FAIL"


def test_condition_body_for_any_app():
    root = Path(__file__).resolve().parents[3]
    body = harness.condition_body(root, "slow-database", "qtt")
    assert body["id"] == "slow-database" and body["db"]["latency_ms"] == 250
