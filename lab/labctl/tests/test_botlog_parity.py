import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from labctl.botlog import parity, recording, robotlog, trace
from labctl.botlog.cli import app as botlog_app
from labctl.botlog.cli import load_analysis
from labctl.models import Session

ROOT = Path(__file__).resolve().parents[3]
FIX = Path(__file__).parent / "fixtures" / "botlog"
AUTOMATION = ROOT / "automations" / "uipath-case-update"
RECS = sorted((FIX / "recordings" / "showcase").iterdir())
runner = CliRunner()


@pytest.fixture(scope="module")
def analysis():
    return load_analysis(AUTOMATION)


@pytest.fixture(scope="module")
def golden(analysis):
    run = robotlog.parse(FIX / "robot.log.jsonl")[0]
    return trace.build(analysis, run, recording.load(RECS[0]), source="harness")


@pytest.fixture()
def agent(analysis):
    """The same run, seen as an agent's recording."""
    return trace.from_recording(recording.load(RECS[0]), analysis)


def test_harness_bot_against_itself_passes(golden, agent):
    report = parity.compare(golden, agent)
    assert report.status == "pass", report.markdown()
    assert report.findings == []
    checks = {c.check: c.status for c in report.checks}
    assert checks == {
        "screens": "pass",
        "fields": "pass",
        "values": "pass",
        "requests": "pass",
        "outcome": "skipped",
    }
    assert parity.compare(golden, golden).status == "pass"


def test_skipping_a_field_fails_with_the_field_named(golden, agent):
    agent.steps = [s for s in agent.steps if s.field != "in_Subcategory"]
    report = parity.compare(golden, agent)
    assert report.status == "fail"
    [f] = [f for f in report.findings if f.check == "fields"]
    assert f.severity == "fail" and f.field == "in_Subcategory"
    assert "in_Subcategory" in report.markdown()


def test_wrong_value_fails_showing_both_values(golden, agent):
    step = next(s for s in agent.steps if s.field == "in_CaseNo")
    step.value = "C-9999"
    report = parity.compare(golden, agent)
    assert report.status == "fail"
    [f] = [f for f in report.findings if f.check == "values"]
    assert (f.field, f.expected, f.actual) == ("in_CaseNo", "C-1001", "C-9999")
    md = report.markdown()
    assert "C-1001" in md and "C-9999" in md


def test_values_compare_after_masking(golden, agent):
    next(s for s in agent.steps if s.field == "in_CaseNo").value = "c-1001 "
    assert parity.compare(golden, agent).status == "fail"
    report = parity.compare(golden, agent, mask=lambda v: str(v).strip().upper())
    assert report.status == "pass"


def test_extra_screen_and_reorder_warn(golden, agent):
    extra = agent.steps[3].model_copy(
        update={"screen": "help", "field": None, "selector": "a#help"}
    )
    agent.steps.insert(5, extra)
    report = parity.compare(golden, agent)
    assert report.status == "warn"
    assert {f.check for f in report.findings} == {"screens", "fields"}
    assert all(f.severity == "warn" for f in report.findings)


def test_missing_request_fails_and_extra_warns(golden, agent):
    for s in agent.steps:
        s.requests = [r for r in s.requests if r != "PUT /api/cases/*"] + ["GET /api/extra"]
    report = parity.compare(golden, agent)
    sev = {f.what: f.severity for f in report.findings}
    assert sev == {
        "request PUT /api/cases/* never made": "fail",
        "extra request GET /api/extra": "warn",
    }


def _session(changes, requests=()):
    return Session.model_validate(
        {
            "session_id": "ses_20261120T101500Z_a41c9e",
            "apps": [{"app": "showcase", "version": "0.1.0"}],
            "started": "2026-11-20T10:15:00Z",
            "requests": [
                {
                    "ts": "2026-11-20T10:15:08Z",
                    "app": "showcase",
                    "method": m,
                    "path": p,
                    "status": 200,
                    "app_ms": 5,
                }
                for m, p in requests
            ],
            "changes": [{"app": "showcase", "table": t, "op": op, "key": "1"} for t, op in changes],
        }
    )


def test_outcome_from_lab_sessions(golden, agent):
    bot = _session([("cases", "update"), ("case_note", "insert")])
    same = parity.compare(
        golden,
        agent,
        base_session=bot,
        other_session=_session([("cases", "update"), ("case_note", "insert")]),
    )
    assert same.status == "pass"
    missing = parity.compare(
        golden, agent, base_session=bot, other_session=_session([("cases", "update")])
    )
    assert missing.status == "fail"
    assert any(f.check == "outcome" and "case_note" in f.what for f in missing.findings)


def test_session_request_log_alone_is_unverified(golden):
    keys = {r for s in golden.steps for r in s.requests}
    reqs = [
        (m, (p[:-1] if p.endswith("search*") else p).replace("*", "C-1001"))
        for m, p in (k.split() for k in keys)
    ]
    report = parity.compare(golden, _session([], reqs + [("GET", "/assets/x.js")]))
    assert report.status == "unverified"
    assert {c.check: c.status for c in report.checks}["requests"] == "pass"


def test_cli_parity_json_and_markdown(tmp_path, golden):
    base = tmp_path / "golden.json"
    base.write_text(golden.model_dump_json(), encoding="utf-8")
    args = ["parity", str(base), str(RECS[1]), "--automation", str(AUTOMATION)]
    args += ["--json", str(tmp_path / "p.json"), "--md", str(tmp_path / "p.md")]
    result = runner.invoke(botlog_app, args)
    assert result.exit_code == 1  # the second run used another case
    data = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))
    assert data["status"] == "fail"
    assert "C-1005" in (tmp_path / "p.md").read_text(encoding="utf-8")
    result = runner.invoke(
        botlog_app, ["parity", str(base), str(RECS[0]), "--automation", str(AUTOMATION)]
    )
    assert result.exit_code == 0, result.output
