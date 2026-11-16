"""Round 4 in the portal: the session verdict and its accept button, named packs, baseline
history, fidelity replay and recording a running browser. The lab agent is a stand-in."""

from __future__ import annotations

import yaml
from fastapi.testclient import TestClient
from test_portal import ROOT, repo, wait_job  # noqa: F401 - the fixture

from labctl import lab as lab_mod
from labctl import packs as packs_mod
from labctl.portal.app import create_app

SESSION = "ses_20261116T120000Z_abcdef"


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.loaded = []

    def info(self):
        return {"baseline": {"id": "bl_20261116T110000Z_aaaaaa", "label": "seed"}, "tables": [],
                "session": None}  # fmt: skip

    def sessions(self):
        return [{"session_id": SESSION, "automation": "uipath-case-update", "summary": {}}]

    def condition(self):
        return {"id": None}

    def baselines(self):
        return [{"id": "bl_20261116T110000Z_aaaaaa", "loaded_at": "2026-11-16T11:00:00Z",
                 "kind": "load", "counts": {"cases": 3}, "current": True, "pinned": False},
                {"id": "bl_20261116T100000Z_bbbbbb", "loaded_at": "2026-11-16T10:00:00Z",
                 "kind": "snapshot", "counts": {"cases": 2}, "current": False, "pinned": True}]  # fmt: skip

    def restore_baseline(self, bid):
        self.calls.append(("restore", bid))
        return {"counts": {"cases": 2}}

    def pin_baseline(self, bid, pinned):
        self.calls.append(("pin", bid, pinned))
        return {}

    def data(self):
        return {"cases": [{"id": 1}, {"id": 2}]}

    def load(self, rows, label, pack=None):
        self.loaded.append((label, pack))
        return {"counts": {t: len(r) for t, r in rows.items()}}

    def session(self, sid):
        return {"session_id": sid, "automation": "uipath-case-update", "apps": [{"app": "showcase"}],
                "started": "2026-11-16T12:00:00Z", "summary": {}, "requests": [],
                "changes": [
                    {"app": "showcase", "table": "cases", "op": "update", "key": "1",
                     "columns": ["status"]},
                    {"app": "showcase", "table": "loans", "op": "delete", "key": "9", "columns": []},
                ]}  # fmt: skip

    def session_detail(self, sid):
        return {"tables_used": [{"table": "cases", "read": True, "write": True}]}


def setup(repo, monkeypatch):  # noqa: F811
    fake = FakeAgent()
    monkeypatch.setattr(lab_mod, "agent", lambda root, app_id, host=None: fake)
    app_dir = repo / "apps" / "showcase"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "app.yaml").write_text(yaml.safe_dump({
        "id": "showcase", "name": "Showcase", "copies": {"system": "Case Workbench"},
        "stack": {}, "run": {"web": {"port": 8000}},
        "lab": {"port": 8720, "db_port": 15720, "credentials": {"password": "pw"}},
    }), encoding="utf-8")  # fmt: skip
    (app_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    monkeypatch.setattr(
        lab_mod, "status",
        lambda root, app_id: lab_mod.Status(app_id, True, {"app": "running"}, None),
    )  # fmt: skip
    return fake


def test_session_verdict_and_accept(repo, monkeypatch):  # noqa: F811
    setup(repo, monkeypatch)
    path = repo / "automations/uipath-case-update/automation.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc.pop("expects", None)  # start from an automation that declares nothing
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    client = TestClient(create_app(repo))
    page = client.get(f"/apps/showcase/sessions/{SESSION}").text
    assert "NOT DECLARED" in page and "These changes are correct" in page
    assert "Tables used" in page and "<code>cases</code>" in page
    r = client.post(f"/apps/showcase/sessions/{SESSION}/accept", data={})
    assert "now expects cases: update; loans: delete" in r.text
    page = client.get(f"/apps/showcase/sessions/{SESSION}").text
    assert ">PASS<" in page and "These changes are correct" not in page
    # narrow what is expected by hand: the delete becomes unexpected
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["expects"] = [{"table": "cases", "ops": ["update"], "columns": ["status"]}]
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    page = client.get(f"/apps/showcase/sessions/{SESSION}").text
    assert ">FAIL<" in page and "table loans is not expected to change" in page


def test_packs_and_baselines_on_the_data_card(repo, monkeypatch):  # noqa: F811
    fake = setup(repo, monkeypatch)
    client = TestClient(create_app(repo))
    r = client.post("/apps/showcase/packs/save", data={"name": "month-end"})
    assert "Saved pack month-end" in r.text
    assert [p.name for p in packs_mod.listing(repo, "showcase")] == ["month-end"]
    page = client.get("/apps/showcase").text
    assert "month-end" in page and "Earlier baselines (2)" in page and ">Restore<" in page
    r = client.post("/apps/showcase/packs/month-end/use")
    assert "Loaded pack month-end" in r.text and fake.loaded[-1] == ("month-end", "month-end")
    r = client.post("/apps/showcase/baselines/bl_20261116T100000Z_bbbbbb/restore")
    assert "Restored the baseline" in r.text
    client.post("/apps/showcase/baselines/bl_20261116T100000Z_bbbbbb/pin", data={"pinned": ""})
    assert fake.calls == [("restore", "bl_20261116T100000Z_bbbbbb"),
                          ("pin", "bl_20261116T100000Z_bbbbbb", False)]  # fmt: skip
    r = client.post("/apps/showcase/packs/save", data={"name": "../x"})
    assert "lower-case letters" in r.text
    client.post("/apps/showcase/packs/month-end/delete")
    assert packs_mod.listing(repo, "showcase") == []


def test_fidelity_replay_job_and_report(repo, monkeypatch):  # noqa: F811
    setup(repo, monkeypatch)
    rec = repo / "evidence/showcase/r1"
    (rec / "dom").mkdir(parents=True)
    (rec / "steps.jsonl").write_text("{}\n", encoding="utf-8")
    (rec / "recording.har").write_text('{"log": {"entries": []}}', encoding="utf-8")
    seen = {}

    from labctl.behaviour import fidelity

    def fake_replay(folder, base_url, values, headless=True):
        seen.update(base=base_url, values=values)
        return fidelity.FidelityReport(recording="r1", base_url=base_url, status="pass", steps=[])

    monkeypatch.setattr(fidelity, "replay", fake_replay)
    client = TestClient(create_app(repo))
    assert "Fidelity replay" in client.get("/apps/showcase").text
    r = client.post("/apps/showcase/fidelity", data={"recording": "r1", "values": "#u=ann"},
                    follow_redirects=False)  # fmt: skip
    job = wait_job(client, r.headers["location"])
    assert job["status"] == "done", job["log"]
    assert seen["base"] == "http://127.0.0.1:8720"
    assert seen["values"]["#u"] == "ann" and seen["values"]["input[type=password]"] == "pw"
    report = job["result"]["report"]
    assert "Result: pass" in client.get(report).text
    assert client.get("/apps/showcase/fidelity/..%2Fsecret").status_code == 404


def test_attach_only_to_this_machine(repo, monkeypatch):  # noqa: F811
    setup(repo, monkeypatch)
    client = TestClient(create_app(repo))
    r = client.post("/apps/showcase/record", data={"name": "r2", "cdp": "http://10.0.0.5:9222"})
    assert "Attach only to a browser on this machine" in r.text
    r = client.post("/apps/showcase/record", data={"name": "r2"})
    assert "Give a start address" in r.text
    r = client.post("/apps/showcase/record/stop", data={"name": "r2"})
    assert "Stopping the recording r2" in r.text
    assert (repo / ".lab-state/record-stop/showcase--r2").exists()
