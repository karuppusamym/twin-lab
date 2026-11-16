import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from labctl.behaviour import catalog, dom, fidelity
from labctl.behaviour.cli import app as behaviour_app
from labctl.botlog import recording

FIX = Path(__file__).parent / "fixtures" / "botlog"
SHOWCASE = FIX / "recordings" / "showcase"
RECS = sorted(SHOWCASE.iterdir())
runner = CliRunner()


@pytest.fixture(scope="module")
def cat():
    return catalog.build(RECS)


def rule(cat, kind, trigger, target):
    found = [
        r for r in cat.rules if (r.kind, r.trigger.element, r.target) == (kind, trigger, target)
    ]
    assert found, f"no {kind} rule {trigger} -> {target}"
    return found[0]


# --- catalog -----------------------------------------------------------------------------------


def test_category_cascade_replaces_subcategory_options(cat):
    r = rule(cat, "set_options", "#category", "#subcategory")
    assert r.trigger.event == "change"
    assert r.observations == 3 and r.single_observation is False
    examples = {e["when"]: e["options"] for e in r.detail["examples"]}
    assert examples["Cards"] == ["Select…", "Declined payment", "Lost or stolen"]
    assert examples["Payments"] == ["Select…", "Delayed", "Duplicate charge", "Refund"]
    assert r.screen == "/cases/*"
    assert "- When #category changes, #subcategory options are replaced (seen 3 times)" in (
        catalog.brief_lines(cat)
    )


def test_escalation_reason_appears_when_status_is_escalated(cat):
    r = rule(cat, "show", "#caseStatus", "#escalationReason")
    assert r.trigger.values == ["ESCALATED"]
    assert r.trigger.not_values == ["IN_PROGRESS"]
    assert r.observations == 2
    line = catalog.describe(r)
    assert line.startswith("When #caseStatus changes to ESCALATED, #escalationReason appears")


def test_other_kinds_found_on_the_showcase(cat):
    assert rule(cat, "enable", "#password", "#btnLogin").observations == 3
    assert rule(cat, "navigate", "#btnLogin", "/cases").observations == 3
    assert rule(cat, "show", "#btnSave", "#message").observations == 3
    sub_enable = rule(cat, "enable", "#category", "#subcategory")
    assert sub_enable.single_observation is True
    assert "single observation" in catalog.describe(sub_enable)
    delay = rule(cat, "delay", "#category", "#subcategory")
    assert delay.detail["requests"] == ["GET /api/categories/*/subcategories"]
    assert delay.detail["max_ms"] >= 0 and len(delay.detail["ms"]) == 3


def test_every_rule_has_an_evidence_pair_that_exists(cat):
    assert cat.rules
    for r in cat.rules:
        assert r.evidence, r.id
        assert r.single_observation == (r.observations == 1)
        for e in r.evidence:
            base = SHOWCASE / e.recording
            assert (base / e.before).exists() and (base / e.after).exists()


def test_no_rule_without_snapshots(tmp_path):
    rec = tmp_path / "showcase" / "rec_x"
    rec.mkdir(parents=True)
    src = RECS[0]
    (rec / "steps.jsonl").write_text(
        (src / "steps.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert catalog.build([rec]).rules == []


def test_typed_values_and_page_text_are_kept_as_shapes_only(cat):
    text = cat.model_dump_json()
    assert "Ben Okafor" not in text and "C-1001" not in text
    assert rule(cat, "set_value", "#btnSave", "#fStatus").detail["values"] == ["A_A"]


def test_cli_catalog_writes_behaviours_json(tmp_path):
    out = tmp_path / "apps" / "showcase" / "behaviours.json"
    result = runner.invoke(behaviour_app, ["catalog", str(SHOWCASE), "--out", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["app"] == "showcase" and len(data["recordings"]) == 3
    assert "options are replaced" in result.output


# --- fidelity without a live app ----------------------------------------------------------------


def test_identical_snapshots_compare_clean():
    html = (RECS[0] / "dom" / "0009.html").read_text(encoding="utf-8")
    assert fidelity.compare_dom(html, html) == []


def test_removed_option_fails_naming_the_element(tmp_path):
    recorded = RECS[0] / "dom" / "0009.html"
    html = recorded.read_text(encoding="utf-8")
    edited = re.sub(r'<option value="\d+">Lost or stolen</option>', "", html)
    assert edited != html
    live = tmp_path / "live.html"
    live.write_text(edited, encoding="utf-8")
    [d] = fidelity.compare_files(recorded, live, step=9)
    assert (d.step, d.element, d.check) == (9, "#subcategory", "options")
    assert "Lost or stolen" in d.expected and "Lost or stolen" not in d.actual
    result = runner.invoke(behaviour_app, ["fidelity", str(recorded), str(live)])
    assert result.exit_code == 1 and "#subcategory options" in result.output


def test_visibility_enabled_set_and_value_shape():
    before = (RECS[0] / "dom" / "0010.html").read_text(encoding="utf-8")  # subcategory chosen
    after = (RECS[0] / "dom" / "0011.html").read_text(encoding="utf-8")  # status ESCALATED
    diffs = {(d.element, d.check) for d in fidelity.compare_dom(after, before)}
    assert ("input#escalationReason", "missing") in diffs
    disabled = after.replace('id="btnSave" type="submit"', 'id="btnSave" type="submit" disabled=""')
    assert [(d.element, d.check) for d in fidelity.compare_dom(after, disabled)] == [
        ("#btnSave", "enabled")
    ]
    typed = (RECS[0] / "dom" / "0006.html").read_text(encoding="utf-8")
    other_data = typed.replace('value="C-1001"', 'value="C-2002"')
    assert fidelity.compare_dom(typed, other_data) == []  # same shape
    bad = typed.replace('value="C-1001"', 'value="1001"')
    [d] = fidelity.compare_dom(typed, bad)
    assert (d.element, d.check, d.expected, d.actual) == ("#caseNo", "value_format", "A-9", "9")


def test_dom_reader_basics():
    snap = dom.snapshot(
        '<div hidden><input id="a" value="x"></div><select id="s" disabled>'
        '<option value="1">One</option><option value="2" selected>Two</option></select>'
        '<input type="password" id="p"><button id="b">Go</button>'
    )
    assert snap.elements["#a"].visible is False
    s = snap.elements["#s"]
    assert (s.enabled, s.value, s.selected, s.options) == (False, "2", "Two", ["One", "Two"])
    assert snap.elements["#p"].value is None
    assert dom.key_of("table#results td.status") == "#results"
    assert dom.shape("12,500.00") == "9,9.9"


def test_recording_reader():
    rec = recording.load(RECS[0])
    assert rec.app == "showcase"
    assert [s.action for s in rec.steps][:4] == ["goto", "type", "type", "click"]
    assert rec.steps[2].value is None  # the password is not recorded
    keys = [e.key() for e in rec.entries if not e.is_asset()]
    assert "POST /api/login" in keys and "GET /api/cases/search*" in keys
