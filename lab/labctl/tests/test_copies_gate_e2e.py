"""Per-run lab copies, the fidelity gate in readiness, ATS-compatible bindings and the e2e report.
No Docker needed: what Docker would be told is checked, not run."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import yaml

from labctl import bindings, conformance, copies, e2e, lab
from labctl.behaviour import fidelity
from labctl.models import Automation


@pytest.fixture
def root(tmp_path, monkeypatch):
    app = tmp_path / "apps" / "demo"
    (app / "db").mkdir(parents=True)
    (app / "db" / "schema.sql").write_text("CREATE TABLE t (id INT PRIMARY KEY);\n", "utf-8")
    (app / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (app / "app.yaml").write_text(yaml.safe_dump({
        "id": "demo", "name": "Demo", "copies": {"system": "Demo"}, "stack": {},
        "run": {"web": {"port": 8000}, "api": {"port": 8000, "health": "/api/health"}},
        "lab": {"port": 8750, "db_port": 15750, "credentials": {"username": "u", "password": "p"}},
    }), encoding="utf-8")  # fmt: skip
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / ".lab-state"))
    return tmp_path


def _copy(root: Path, name: str = "run-1", port: int = 18001, db_port: int = 18002) -> str:
    lab_id = f"demo.{name}"
    copies._record_path(root, lab_id).write_text(yaml.safe_dump(
        {"lab": lab_id, "app": "demo", "name": name, "port": port, "db_port": db_port,
         "data": "app", "created": "2026-11-16T00:00:00+00:00"}), encoding="utf-8")  # fmt: skip
    return lab_id


def automation(uses=("button#save", "input#caseNo")) -> Automation:
    return Automation.model_validate({
        "id": "bot", "name": "bot", "type": "python", "surface": "frontend", "source": {"path": "."},
        "bindings": {"url": "{{demo.web}}", "who": "{{demo.credentials.username}}"},
        "touches": [{"app": "demo", "via": "web", "screens": [
            {"name": "cases", "path": "/cases", "uses": [{"action": "click", "selector": s} for s in uses]}]}],
    })  # fmt: skip


# --- copies ------------------------------------------------------------------------------------


def test_a_copy_is_the_same_app_on_its_own_ports_and_project(root):
    lab_id = _copy(root)
    app = bindings.load_app(root, lab_id)
    assert app.id == "demo" and (app.lab.port, app.lab.db_port) == (18001, 18002)
    doc = lab.compose(root, lab_id)
    assert (
        doc["name"] == "twinlab-demo_run-1" and lab.compose(root, "demo")["name"] == "twinlab-demo"
    )
    agent = doc["services"]["agent"]
    assert agent["ports"] == ["127.0.0.1:18001:9000", "127.0.0.1:18002:15432"]
    assert agent["environment"]["APP_ID"] == "demo"  # sessions name the app, not the copy
    assert doc["services"]["db"]["environment"]["POSTGRES_DB"] == "demo"
    assert doc["services"]["app"]["image"] == "twinlab/demo:dev"  # the app's image, not rebuilt
    assert doc["services"]["app"]["build"]["context"].endswith("apps/demo")


def test_an_unknown_copy_is_refused_plainly(root):
    with pytest.raises(bindings.BindingError, match="no copy 'nope' of demo"):
        bindings.load_app(root, "demo.nope")


def test_free_ports_skip_the_apps_and_other_copies(root):
    _copy(root, port=18000, db_port=18001)
    ports = copies.free_ports(root)
    assert len(ports) == 2 and not {18000, 18001, 8750, 15750} & set(ports)
    assert copies._next_name(root, "demo") == "run-2"


def test_copies_refuse_a_dba_server(root):
    path = root / "apps/demo/app.yaml"
    data = yaml.safe_load(path.read_text())
    data["lab"]["database"] = "qtt-sql"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(copies.CopyError, match="database of its own"):
        copies.create(root, "demo")


def test_dropping_needs_a_copy(root):
    with pytest.raises(copies.CopyError, match="not a copy"):
        copies.drop(root, "demo")


def test_bindings_point_at_the_copy_but_keep_the_app_name(root):
    lab_id = _copy(root)
    b = bindings.for_automation(root, automation(), host="127.0.0.1", labs={"demo": lab_id})
    assert list(b.apps) == ["demo"] and b.apps["demo"].web == "http://127.0.0.1:18001"
    assert "@127.0.0.1:18002/demo" in b.apps["demo"].db


# --- ATS-compatible bindings -------------------------------------------------------------------


def test_bindings_carry_application_twin_studio_names(root):
    b = bindings.for_automation(root, automation(), host="127.0.0.1")
    doc = json.loads(bindings.render(automation(), b, "json"))
    # what ats/sdk.py Twin reads: apps[app]["http"], credentials, arguments, output_path
    assert doc["apps"]["demo"]["http"] == "http://127.0.0.1:8750"
    assert doc["apps"]["demo"]["credentials"] == {"username": "u", "password": "p"}
    assert doc["arguments"] == {"url": "http://127.0.0.1:8750", "who": "u"}
    assert doc["output_path"] == "result.json"


# --- the fidelity gate -------------------------------------------------------------------------


def _report(root: Path, steps: list[dict], recording: str = "flow", age_s: float = 0) -> None:
    rep = fidelity.FidelityReport(recording=recording, status="fail" if any(
        s["status"] == "fail" for s in steps) else "pass", steps=steps)  # fmt: skip
    fidelity.save(root, "demo", rep)
    if age_s:  # pretend the app changed after the replay
        later = time.time() + age_s
        os.utime(root / "apps/demo/Dockerfile", (later, later))


def _fidelity_items(report):
    return [i for i in report.items if i.kind == "fidelity"]


def test_no_replay_is_noted_or_blocks_when_required(root):
    rep = conformance.Report("bot")
    conformance._check_fidelity(root, automation(), rep, require=False)
    assert [i.status for i in _fidelity_items(rep)] == ["unchecked"]
    rep = conformance.Report("bot")
    conformance._check_fidelity(root, automation(), rep, require=True)
    assert [i.status for i in _fidelity_items(rep)] == ["failed"]


def test_a_failed_replay_on_what_the_bot_uses_blocks(root):
    _report(root, [{"seq": 1, "action": "click", "selector": "button#save", "status": "fail",
                    "differences": [{"step": 1, "element": "button#save", "check": "missing"}]}])  # fmt: skip
    rep = conformance.Report("bot")
    conformance._check_fidelity(root, automation(), rep, require=False)
    [item] = _fidelity_items(rep)
    assert item.status == "failed" and "button#save" in item.detail


def test_a_failed_replay_elsewhere_is_noted_not_blocking(root):
    _report(root, [{"seq": 1, "action": "click", "selector": "a#help", "status": "fail",
                    "differences": [{"step": 1, "element": "a#help", "check": "missing"}]}])  # fmt: skip
    rep = conformance.Report("bot")
    conformance._check_fidelity(root, automation(), rep, require=True)
    [item] = _fidelity_items(rep)
    assert item.status == "ok" and rep.errors and "does not use" in rep.errors[0]


def test_a_passed_replay_is_ok_unless_the_app_changed_since(root):
    _report(root, [{"seq": 1, "action": "click", "selector": "button#save", "status": "pass"}])
    rep = conformance.Report("bot")
    conformance._check_fidelity(root, automation(), rep, require=True)
    assert [i.status for i in _fidelity_items(rep)] == ["ok"]
    time.sleep(1.1)  # the next report gets a later stamp
    _report(root, [{"seq": 1, "action": "click", "selector": "button#save", "status": "pass"}],
            age_s=120)  # fmt: skip
    rep = conformance.Report("bot")
    conformance._check_fidelity(root, automation(), rep, require=True)
    [item] = _fidelity_items(rep)
    assert item.status == "failed" and "replay again" in item.detail


def test_the_newest_report_wins(root):
    _report(root, [{"seq": 1, "action": "click", "selector": "button#save", "status": "fail"}])
    time.sleep(1.1)
    _report(root, [{"seq": 1, "action": "click", "selector": "button#save", "status": "pass"}])
    name, rep = fidelity.latest(root, "demo")
    assert rep.status == "pass" and name.startswith("flow--")


# --- e2e report --------------------------------------------------------------------------------


def test_e2e_passes_only_when_every_stage_passed(tmp_path):
    r = e2e.E2EResult("bot", app="demo", lab="demo.run-1")
    for name in ("contracts", "lab", "copy", "readiness", "bot run", "fidelity", "cleanup"):
        r.stage(name).status = "PASS"
    assert e2e._finish(r, tmp_path).status == "PASS"
    r.stages[5].status = "SKIP"
    r.stages[5].detail = "no recording | to replay"
    done = e2e._finish(r, tmp_path)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert (
        done.status == "FAIL"
        and "| fidelity | SKIP |" in text
        and "no recording / to replay" in text
    )
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "FAIL"


def test_a_stage_that_raises_is_recorded_and_the_run_goes_on():
    r = e2e.E2EResult("bot")
    with e2e._Timer(r.stage("lab")):
        raise RuntimeError("Docker is not running")
    assert r.stages[0].status == "FAIL" and "Docker is not running" in r.stages[0].detail
