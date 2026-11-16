import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from labctl.botlog import netlog, recording, robotlog, trace
from labctl.botlog.cli import app as botlog_app
from labctl.botlog.cli import load_analysis
from labctl.models import Condition

ROOT = Path(__file__).resolve().parents[3]
FIX = Path(__file__).parent / "fixtures" / "botlog"
AUTOMATION = ROOT / "automations" / "uipath-case-update"
RECS = sorted((FIX / "recordings" / "showcase").iterdir())
runner = CliRunner()


@pytest.fixture(scope="module")
def analysis():
    return load_analysis(AUTOMATION)


@pytest.fixture(scope="module")
def traces(analysis):
    runs = robotlog.parse(FIX / "robot.log.jsonl")
    return [
        trace.build(analysis, r, recording.load(p), source="harness")
        for r, p in zip(runs, RECS, strict=True)
    ]


def test_trace_reproduces_the_scenario(traces):
    t = traces[0]
    got = [(s.screen, s.action, s.field or s.selector) for s in t.steps]
    assert got == [
        ("login", "goto", None),
        ("login", "type", "in_User"),
        ("login", "type", "in_Password"),
        ("login", "click", "button#btnLogin"),
        ("cases", "goto", None),
        ("cases", "type", "in_CaseNo"),
        ("cases", "click", "button#btnSearch"),
        ("cases", "wait", "found"),
        ("cases", "read", "currentStatus"),
        ("cases-item", "goto", None),
        ("cases-item", "select", "in_Category"),
        ("cases-item", "select", "in_Subcategory"),
        ("cases-item", "select", "select#caseStatus"),
        ("cases-item", "click", "button#btnSave"),
        ("cases-item", "read", "out_Message"),
    ]
    assert all(s.app == "showcase" for s in t.steps)


def test_values_and_redaction(traces):
    s = {(x.field or x.selector): x for x in traces[0].steps if x.action != "goto"}
    assert s["in_CaseNo"].value == "C-1001"
    assert s["in_Category"].value == "Cards"
    assert s["found"].value is True
    assert s["currentStatus"].value == "IN_PROGRESS"
    assert s["in_Password"].redacted is True and s["in_Password"].value is None
    assert s["in_Password"].matched_by == "order"
    assert any("matched to the next analysed use by order" in g.what for g in traces[0].gaps)


def test_timing_and_requests(traces):
    steps = traces[0].steps
    for a, b in zip(steps, steps[1:], strict=False):
        assert a.duration_ms is not None and a.gap_ms is not None
        assert a.wait_ms == a.duration_ms + a.gap_ms
        assert a.ts <= b.ts
    by = {(x.field or x.selector): x for x in steps}
    assert "GET /api/cases/search*" in by["button#btnSearch"].requests
    assert by["in_Category"].requests == ["GET /api/categories/*/subcategories"]
    assert by["button#btnSave"].requests == ["PUT /api/cases/*"]
    assert by["in_Category"].dom == "dom/0009.html"


def test_wait_statistics_across_runs(traces):
    stats = {(w.screen, w.target): w for w in trace.wait_stats(traces)}
    w = stats[("cases-item", "in_Category")]
    assert w.n == 3
    assert 0 <= w.p50_ms <= w.p95_ms <= w.max_ms


def test_calibrate_gives_a_valid_condition_fragment(traces):
    frag = trace.calibrate(traces)
    assert frag["calibration"] == "robot_logs"
    assert frag["app"] == "showcase"
    matches = [r["match"] for r in frag["http"]]
    assert "GET /api/cases/search*" in matches and "PUT /api/cases/*" in matches
    # it slots into a condition profile as one target
    Condition.model_validate(
        {
            "id": "calibrated",
            "calibration": frag["calibration"],
            "calibrated_from": frag["calibrated_from"],
            "targets": [{"app": frag["app"], "http": frag["http"], "db": frag["db"]}],
        }
    )
    assert frag["steps"]


def test_netlog_requests_join_by_time(analysis):
    run = robotlog.parse(FIX / "robot.log.jsonl")[0]
    click = next(s for s in run.steps if s.display == "Click button#btnSearch")
    reqs = [
        netlog.NetRequest(
            source_id=1, url="http://x/api/cases/search?q=1", method="GET", start=click.ts
        ),
        netlog.NetRequest(source_id=2, url="http://x/assets/app.js", method="GET", start=click.ts),
    ]
    t = trace.build(analysis, run, None, reqs)
    step = next(s for s in t.steps if s.selector == "button#btnSearch")
    assert step.requests == ["GET /api/cases/search*"]


def test_trace_without_analysis_lists_gaps_instead_of_failing():
    run = robotlog.parse(FIX / "robot.log.jsonl")[0]
    t = trace.build(None, run)
    assert len(t.steps) == 15
    assert t.gaps
    assert next(s for s in t.steps if s.action == "type").selector == "input#username"


def test_cli_trace_and_calibrate(tmp_path):
    out = tmp_path / "traces.json"
    args = [
        "trace",
        str(FIX / "robot.log.jsonl"),
        "--automation",
        str(AUTOMATION),
        "--out",
        str(out),
    ]
    args += [a for p in RECS for a in ("--recording", str(p))]
    result = runner.invoke(botlog_app, [*args, "--source", "harness"])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [d["recording"] for d in data] == [p.name for p in RECS]
    frag = tmp_path / "cond.yaml"
    result = runner.invoke(botlog_app, ["calibrate", str(out), "--out", str(frag)])
    assert result.exit_code == 0, result.output
    assert "calibration: robot_logs" in frag.read_text(encoding="utf-8")
