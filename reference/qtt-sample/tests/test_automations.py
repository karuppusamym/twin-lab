"""The automations written against this sample still fit it: the Python SLA report runs against
the app in process, and every selector the UiPath project uses exists on the page it names."""

import importlib.util
import json
import re
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.seed import TEST_PASSWORD

REPO = Path(__file__).resolve().parents[3]
REPORT = REPO / "automations" / "python-qtt-sla-report" / "report.py"
UIPATH = REPO / "automations" / "uipath-qtt-qc-review" / "automation.yaml"


def load_report():
    spec = importlib.util.spec_from_file_location("qtt_sla_report", REPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sla_report_runs_against_the_sample(qtt, tmp_path, monkeypatch):
    report = load_report()
    monkeypatch.setattr(report.httpx, "Client", lambda base_url, timeout: TestClient(qtt))
    out = tmp_path / "result.json"
    bindings = {
        "apps": {
            "qtt": {
                "api": "http://testserver",
                "db": f"sqlite:///{qtt.state.db_path.as_posix()}",
                "credentials": {"username": "reviewer1", "password": TEST_PASSWORD},
            }
        },
        "inputs": {"as_of": "2026-11-15T12:00:00Z", "sla_hours": "72"},
        "output_path": str(out),
    }
    monkeypatch.setenv("ATS_BINDINGS", json.dumps(bindings))
    assert report.main() == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["status"] == "DONE" and result["sla_hours"] == 72
    assert result["submitted"] == 10 == len(result["reviews"])
    first = result["reviews"][0]
    assert first["review_no"] == "R-1003" and first["hours_waiting"] > 72 and first["breached"]


def test_sla_report_reads_a_bindings_file(tmp_path, monkeypatch):
    report = load_report()
    path = tmp_path / "bindings.json"
    path.write_text('{"apps": {}}', encoding="utf-8")
    monkeypatch.setenv("ATS_BINDINGS", str(path))
    assert report.load_bindings() == {"apps": {}}


def test_uipath_selectors_exist_on_their_screens(qtt):
    automation = yaml.safe_load(UIPATH.read_text(encoding="utf-8"))
    assert automation["analysis"]["gaps"] == []
    web = next(t for t in automation["touches"] if t["via"] == "web")
    client = TestClient(qtt)
    pages = {"/login": client.get("/login").text}
    analyst = TestClient(qtt)
    analyst.post("/login", data={"username": "analyst1", "password": TEST_PASSWORD})
    pages["/queue"] = analyst.get("/queue", params={"status": "OPEN", "q": "R-1001"}).text
    pages["/reviews/{in_ReviewNo}"] = analyst.get("/reviews/R-1001").text
    for screen in web["screens"]:
        html = pages[screen["path"]]
        for use in screen["uses"]:
            for element_id in re.findall(r"#([\w-]+)", use["selector"]):
                assert f'id="{element_id}"' in html, (screen["path"], use["selector"])
    routes = next(t for t in automation["touches"] if t["via"] == "api")["routes"]
    assert {(r["method"], r["path"]) for r in routes} == {
        ("POST", "/api/login"),
        ("GET", "/api/reviews/{in_ReviewNo}"),
    }
