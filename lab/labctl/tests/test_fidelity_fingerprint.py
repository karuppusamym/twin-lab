"""Durable fidelity results (replication gap R11): a kept replay report says what it compared,
and goes out of date (readiness stops counting it) when any of that changes. No browser and no
Docker: reports are made from a small recording folder written here."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import yaml

from labctl import conformance
from labctl.behaviour import durable, fidelity
from labctl.behaviour.acceptance import CaseResult
from labctl.botlog.recording import load
from labctl.models import Automation


@pytest.fixture
def root(tmp_path, monkeypatch):
    app = tmp_path / "apps" / "demo"
    (app / "db").mkdir(parents=True)
    (app / "db" / "schema.sql").write_text("CREATE TABLE t (id INT PRIMARY KEY);\n", "utf-8")
    (app / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (app / "app.yaml").write_text(yaml.safe_dump({"id": "demo", "name": "Demo", "stack": {},
                                                  "lab": {"port": 8750}}), "utf-8")  # fmt: skip
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / ".lab-state"))
    monkeypatch.setattr(conformance, "image_id", lambda app_id: None)
    return tmp_path


@pytest.fixture
def rec_dir(root):
    rec = root / "evidence" / "demo" / "rec-1"
    (rec / "dom").mkdir(parents=True)
    (rec / "dom" / "0001.html").write_text("<button id='save'>Save</button>", "utf-8")
    (rec / "steps.jsonl").write_text(json.dumps({
        "seq": 1, "ts": "2026-11-17T10:00:00Z", "url": "https://source.example.test/cases",
        "action": "goto", "dom": "dom/0001.html"}) + "\n", "utf-8")  # fmt: skip
    (rec / "bindings.yaml").write_text("version: 1\nsteps: {}\n", "utf-8")
    (rec / "acceptance.yaml").write_text(yaml.safe_dump({"cases": [
        {"id": "saved", "check": "count", "locator": "#save", "equals": 1}]}), "utf-8")  # fmt: skip
    (rec / "baseline.yaml").write_text("version: 1\nlocalStorage: {k: syn}\n", "utf-8")
    (rec / "approved").mkdir()
    (rec / "approved" / "s.png").write_bytes(b"\x89PNG not really")
    (rec / "visual.yaml").write_text(yaml.safe_dump({"states": [
        {"id": "s", "after": "end", "approved": "approved/s.png"}]}), "utf-8")  # fmt: skip
    return rec


def automation() -> Automation:
    return Automation.model_validate({
        "id": "bot", "name": "bot", "type": "python", "surface": "frontend", "source": {"path": "."},
        "touches": [{"app": "demo", "via": "web", "screens": [
            {"name": "cases", "path": "/cases", "uses": [{"action": "click", "selector": "button#save"}]}]}],
    })  # fmt: skip


def keep(
    root: Path, rec_dir: Path, verdict="PASS WITHIN DECLARED SCOPE"
) -> fidelity.FidelityReport:
    rec = load(rec_dir)
    used = {n: rec_dir / f"{n}.yaml" for n in durable.FILES}
    report = fidelity.FidelityReport(
        recording=rec.id, status="pass", verdict=verdict,
        steps=[{"seq": 1, "action": "goto", "status": "pass"}],
        cases=[CaseResult(id="saved", check="count", category="behaviour", status="PASS")],
        fingerprint=durable.for_run(rec, used, {"name": "chromium", "version": "1.0"},
                                    {"width": 1280, "height": 720}))  # fmt: skip
    fidelity.save(root, "demo", report)
    return report


def gate(root, require=True):
    rep = conformance.Report("bot")
    conformance._check_fidelity(root, automation(), rep, require=require)
    return [i for i in rep.items if i.kind == "fidelity"]


def test_a_kept_report_says_what_was_compared(root, rec_dir):
    keep(root, rec_dir)
    _, back = fidelity.latest(root, "demo")
    fp = back.fingerprint
    assert fp["recording"]["digest"] == durable.recording_digest(rec_dir)
    assert fp["recording"]["source_origin"] == "https://source.example.test"
    assert fp["recording"]["captured_at"].startswith("2026-11-17T10:00:00")
    assert all(fp["files"][n]["digest"] for n in durable.FILES)
    assert fp["browser"] == {"name": "chromium", "version": "1.0"}
    assert fp["app"]["id"] == "demo" and fp["app"]["brief"] is None and fp["app"]["schema"]
    assert "database" in fp["app"] and "commit" in fp["app"]
    assert durable.assess(root, "demo", back).state == "current"
    [item] = gate(root)
    assert item.status == "ok"
    assert "rec-1" in durable.describe(fp) and "chromium 1.0" in durable.describe(fp)


CHANGES = {
    "the recording changed": lambda r, root: (r / "dom" / "0001.html").write_text("<p>x</p>", "utf-8"),
    "the bindings file changed": lambda r, root: (r / "bindings.yaml").write_text("steps: {1: a}\n", "utf-8"),
    "the acceptance file changed": lambda r, root: (r / "acceptance.yaml").write_text("cases: []\n", "utf-8"),
    "the baseline file changed": lambda r, root: (r / "baseline.yaml").write_text("version: 1\n", "utf-8"),
    "the visual file changed": lambda r, root: (r / "approved" / "s.png").write_bytes(b"\x89PNG other"),
    "the baseline file was removed": lambda r, root: (r / "baseline.yaml").unlink(),
    "the app was rebuilt": lambda r, root: os.utime(root / "apps/demo/Dockerfile",
                                                    (time.time() + 120, time.time() + 120)),
    "the app's schema changed": lambda r, root: (root / "apps/demo/db/schema.sql").write_text(
        "CREATE TABLE u (id INT);\n", "utf-8"),
    "the database choice changed": lambda r, root: (root / "apps/demo/app.yaml").write_text(
        yaml.safe_dump({"id": "demo", "name": "Demo", "stack": {},
                        "lab": {"port": 8750, "database": "sqlite"}}), "utf-8"),
}  # fmt: skip


@pytest.mark.parametrize("reason", sorted(CHANGES))
def test_any_relevant_change_makes_the_result_out_of_date(root, rec_dir, reason):
    keep(root, rec_dir)
    CHANGES[reason](rec_dir, root)
    _, back = fidelity.latest(root, "demo")
    fresh = durable.assess(root, "demo", back)
    assert fresh.state == "stale" and reason in fresh.reasons, fresh.reasons
    assert not fresh.ready
    [item] = gate(root, require=True)  # readiness does not count it
    assert item.status == "failed" and item.detail.startswith("out of date: ")
    assert reason in item.detail and item.detail.endswith("— replay again")
    [item] = gate(root, require=False)
    assert item.status == "unchecked"


def test_a_file_added_beside_the_recording_after_the_run_is_a_change(root, rec_dir):
    (rec_dir / "visual.yaml").unlink()
    rec = load(rec_dir)
    report = fidelity.FidelityReport(recording=rec.id, status="pass",
                                     fingerprint=durable.for_run(rec, {}, None, None))  # fmt: skip
    fidelity.save(root, "demo", report)
    (rec_dir / "visual.yaml").write_text("states: []\n", "utf-8")
    fresh = durable.assess(root, "demo", fidelity.latest(root, "demo")[1])
    assert "a visual file was added" in fresh.reasons


def test_a_failed_or_blocked_scoped_result_blocks_readiness(root, rec_dir):
    keep(root, rec_dir, verdict="FAIL")
    [item] = gate(root)
    assert item.status == "failed" and item.detail.startswith("acceptance FAIL")


OLD_REPORT = {  # written before R05-R11: no verdict, no cases, no fingerprint
    "recording": "rec-old", "base_url": "http://127.0.0.1:8750", "status": "pass",
    "steps": [{"seq": 1, "action": "click", "selector": "button#save", "status": "pass",
               "differences": []}],
    "coverage": None, "problems": [],
}  # fmt: skip


def test_an_old_format_report_still_loads_and_reads_as_before_tracking(root):
    folder = fidelity.reports_dir(root, "demo")
    folder.mkdir(parents=True)
    (folder / "rec-old--20991231T000000Z.json").write_text(json.dumps(OLD_REPORT), "utf-8")
    name, back = fidelity.latest(root, "demo")
    assert back.status == "pass" and back.verdict is None and back.fingerprint is None
    assert back.cases == [] and back.categories == {}
    assert "**Result: pass**" in back.markdown()
    assert durable.assess(root, "demo", back).state == "legacy"
    [item] = gate(root)
    assert item.status == "ok" and "checked before this was tracked" in item.detail
