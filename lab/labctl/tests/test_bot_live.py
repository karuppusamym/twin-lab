"""Live tests against the showcase demo app on http://localhost:8720. Skipped when it is down or
Playwright is not installed. The run uses a case it cannot change (an escalation without a
reason is refused), so the app's data is left as it was."""

import importlib.util
import json
import urllib.request
from pathlib import Path

import pytest

from labctl.behaviour import catalog, fidelity
from labctl.botlog import parity, recording, robotlog, trace
from labctl.botlog.cli import load_analysis

ROOT = Path(__file__).resolve().parents[3]
FIX = Path(__file__).parent / "fixtures" / "botlog"
AUTOMATION = ROOT / "automations" / "uipath-case-update"
APP = "http://localhost:8720"
INPUTS = {
    "case_no": "C-1005",
    "category": "Payments",
    "subcategory": "Refund",
    "select#caseStatus": "ESCALATED",
}


def _up() -> bool:
    try:
        with urllib.request.urlopen(APP + "/login", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="Playwright missing"),
    pytest.mark.skipif(not _up(), reason=f"showcase app not reachable at {APP}"),
]


def _harness():
    spec = importlib.util.spec_from_file_location("harness_bot", ROOT / "tools" / "harness_bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def harness_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("harness")
    bindings = json.loads((FIX / "bindings.json").read_text(encoding="utf-8"))
    log = out / "robot.log.jsonl"
    [result] = _harness().run(AUTOMATION, bindings, [INPUTS], log, out / "showcase")
    return result, log, Path(result["recording"])


def test_harness_bot_runs_and_logs_the_inputs(harness_run):
    result, log, rec_dir = harness_run
    assert result["state"] == "Successful"
    assert result["read"]["out_Message"].startswith("Escalation needs a reason")
    [run] = robotlog.parse(log)
    typed = [s.values.in_.get("Text") for s in run.steps if s.activity == "TypeInto"]
    assert typed == ["demo.user", None, "C-1005"]  # the password is Private
    assert "workbench-demo" not in log.read_text(encoding="utf-8")
    har = (rec_dir / "recording.har").read_text(encoding="utf-8")
    assert "authorization" not in har.lower() and "workbench-demo" not in har
    assert len(list((rec_dir / "dom").glob("*.html"))) == 12


def test_fidelity_replay_of_a_fresh_recording_passes(harness_run):
    _, _, rec_dir = harness_run
    report = fidelity.replay(rec_dir, APP, {"input#password": "workbench-demo"})
    assert report.status == "pass", report.markdown()
    assert all(s.status == "pass" for s in report.steps)


def test_fidelity_replay_fails_on_an_edited_snapshot(harness_run, tmp_path):
    import shutil

    _, _, rec_dir = harness_run
    copy = tmp_path / "showcase" / rec_dir.name
    shutil.copytree(rec_dir, copy)
    rec = recording.load(copy)
    step = next(s for s in rec.steps if s.selector == "select#category")
    path = copy / step.dom
    html = path.read_text(encoding="utf-8")
    path.write_text(html.replace(">Refund</option>", ">Refunded</option>"), encoding="utf-8")
    report = fidelity.replay(copy, APP, {"input#password": "workbench-demo"})
    assert report.status == "fail"
    d = report.first_failure()
    assert (d.step, d.element, d.check) == (step.seq, "#subcategory", "options")


def test_live_run_feeds_trace_catalog_and_self_parity(harness_run):
    _, log, rec_dir = harness_run
    analysis = load_analysis(AUTOMATION)
    rec = recording.load(rec_dir)
    golden = trace.build(analysis, robotlog.parse(log)[0], rec, source="harness")
    assert parity.compare(golden, trace.from_recording(rec, analysis)).status == "pass"
    cat = catalog.build([rec_dir])
    kinds = {(r.kind, r.trigger.element, r.target) for r in cat.rules}
    assert ("set_options", "#category", "#subcategory") in kinds
    assert ("show", "#caseStatus", "#escalationReason") in kinds
