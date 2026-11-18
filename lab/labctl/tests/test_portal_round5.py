"""Round 5 in the portal: Start here, next steps, who you are, reservations, quarantine,
behaviour comparison. The lab agent is a stand-in."""

from __future__ import annotations

import shutil

from fastapi.testclient import TestClient
from test_portal import ROOT, repo  # noqa: F401 - the fixture
from test_portal_round4 import FakeAgent, setup

from labctl import lab as lab_mod
from labctl.portal import guide
from labctl.portal.app import create_app


class LeaseAgent(FakeAgent):
    def __init__(self):
        super().__init__()
        self.lease_now = None
        self.holders = []

    def info(self):
        return {**super().info(), "lease": self.lease_now}

    def take_lease(self, holder, minutes, purpose):
        self.holders.append(lab_mod.holder())
        self.lease_now = {"holder": holder, "until": "2026-11-16T18:00:00.000Z", "purpose": purpose}
        return self.lease_now

    def release_lease(self, force=False):
        self.calls.append(("release", force))
        self.lease_now = None
        return {"lease": None}

    def quarantine_baseline(self, bid, reason, release=False):
        self.calls.append(("quarantine", bid, reason, release))
        return {}


def start(repo, monkeypatch):  # noqa: F811
    setup(repo, monkeypatch)
    fake = LeaseAgent()
    monkeypatch.setattr(lab_mod, "agent", lambda root, app_id, host=None: fake)
    return fake, TestClient(create_app(repo))


def test_start_page_guides_a_newcomer(repo, monkeypatch):  # noqa: F811
    _, client = start(repo, monkeypatch)
    page = client.get("/start").text
    assert "Your next step" in page and "Say who you are" in page
    assert "Words used here" in page and "Verdict" in page
    r = client.post("/me", data={"name": "Priya N"}, headers={"referer": "http://testserver/start"})
    assert "Hello Priya N" in r.text and "You are <b>Priya N</b>" in r.text
    assert "Say who you are" in r.text and 'class="done"' in r.text
    bad = client.post(
        "/me", data={"name": "<script>"}, headers={"referer": "http://testserver/start"}
    )
    assert "Use letters" in bad.text


def test_app_page_shows_progress_and_the_next_step(repo, monkeypatch):  # noqa: F811
    _, client = start(repo, monkeypatch)
    page = client.get("/apps/showcase").text
    assert 'nav class="progress"' in page and "Next step" in page
    overview = client.get("/").text
    assert "Next step" in overview and 'class="dot' in overview


def test_reserve_needs_a_name_then_holds_the_lab(repo, monkeypatch):  # noqa: F811
    fake, client = start(repo, monkeypatch)
    r = client.post("/apps/showcase/lease/take", data={"minutes": "30"})
    assert "Say who you are first" in r.text
    client.cookies.set("tl_me", "Priya")
    r = client.post("/apps/showcase/lease/take", data={"minutes": "30", "purpose": "month-end"})
    assert "Reserved for you until 2026-11-16T18:00" in r.text
    assert fake.holders == ["Priya"]  # the admin call went out in Priya's name
    assert "You have reserved this lab" in r.text and "Extend" in r.text
    client.cookies.set("tl_me", "Sam")
    page = client.get("/apps/showcase").text
    assert "<b>Priya</b> has reserved this lab" in page and "Release it anyway" in page
    client.post("/apps/showcase/lease/force")
    assert fake.calls[-1] == ("release", True)


def test_quarantine_and_release_a_baseline(repo, monkeypatch):  # noqa: F811
    fake, client = start(repo, monkeypatch)
    bid = "bl_20261116T100000Z_bbbbbb"
    r = client.post(f"/apps/showcase/baselines/{bid}/quarantine", data={"reason": "not masked"})
    assert "Quarantined" in r.text
    client.post(f"/apps/showcase/baselines/{bid}/release")
    assert fake.calls[-2:] == [("quarantine", bid, "not masked", False),
                               ("quarantine", bid, None, True)]  # fmt: skip


def test_behaviour_compare_of_two_recording_sets(repo, monkeypatch):  # noqa: F811
    _, client = start(repo, monkeypatch)
    fix = ROOT / "lab/labctl/tests/fixtures/botlog/recordings/showcase"
    recs = sorted(p for p in fix.iterdir() if p.is_dir())
    for p in recs:
        shutil.copytree(p, repo / "evidence/showcase" / p.name.replace("_", "-").lower())
    names = sorted(p.name for p in (repo / "evidence/showcase").iterdir())
    assert "UAT against production" not in client.get("/apps/showcase").text
    r = client.post("/apps/showcase/behaviour-compare",
                    data={"a": names[:2], "b": names[:2], "name_a": "UAT", "name_b": "production"})  # fmt: skip
    assert "Behaviour: same" in r.text and "UAT against production" in r.text


def test_steps_mark_next_and_optional():
    steps = guide.app_steps(
        "x", has_evidence=True, has_brief=True, built=False, running=False, automations=[],
        ready=[], has_baseline=False, sessions=0, declared=[],
    )  # fmt: skip
    states = {s.id: s.state for s in steps}
    assert states["evidence"] == "done" and states["build"] == "next" and states["lab"] == "todo"
    assert states["readiness"] == "optional" and states["conditions"] == "optional"
    done = guide.app_steps(
        "x", has_evidence=True, has_brief=True, built=True, running=True, automations=["b"],
        ready=["b"], has_baseline=True, sessions=2, declared=["b"], recorded_automations=["b"],
    )  # fmt: skip
    assert guide.next_step(done) is None


def test_each_bot_needs_a_completed_recording():
    recorded = guide.completed_automations(
        [
            {"automation": "a", "ended": "2026-11-17T10:00:00Z"},
            {"automation": "b", "ended": None},
            {"ended": "2026-11-17T10:00:00Z"},
        ]
    )
    steps = guide.app_steps(
        "x",
        has_evidence=True,
        has_brief=True,
        built=True,
        running=True,
        automations=["a", "b"],
        ready=["a", "b"],
        has_baseline=True,
        sessions=3,
        declared=["a", "b"],
        recorded_automations=recorded,
    )
    assert guide.next_step(steps).id == "sessions"
    assert "Still to record: b" in guide.next_step(steps).hint


def test_overview_does_not_invent_data_or_sessions(repo, monkeypatch):  # noqa: F811
    fake, client = start(repo, monkeypatch)
    monkeypatch.setattr(fake, "info", lambda: {})
    monkeypatch.setattr(fake, "sessions", lambda: [])
    overview = client.get("/").text
    assert 'title="Data: todo"' in overview or 'title="Data: next"' in overview
    assert 'title="Sessions: done"' not in overview
    assert 'title="Data: done"' not in overview


def test_start_uses_only_selected_app_and_rejects_unknown(repo, monkeypatch):  # noqa: F811
    _, client = start(repo, monkeypatch)
    # showcase is running, but qtt has not been built in this repository.
    page = client.get("/start?app=qtt").text
    assert 'value="qtt" selected' in page
    assert 'href="/apps/qtt#build"' in page
    assert 'href="/apps/showcase#build"' not in page
    assert "Prepare test data" in page
    assert client.get("/start?app=missing").status_code == 404


def test_agent_failure_keeps_overview_available(repo, monkeypatch):  # noqa: F811
    fake, client = start(repo, monkeypatch)

    def unavailable():
        raise lab_mod.LabError("agent unavailable")

    monkeypatch.setattr(fake, "info", unavailable)
    response = client.get("/")
    assert response.status_code == 200
    assert 'title="Data: done"' not in response.text


def test_saving_name_preserves_selected_application(repo, monkeypatch):  # noqa: F811
    _, client = start(repo, monkeypatch)
    response = client.post(
        "/me", data={"name": "Priya"}, headers={"referer": "http://testserver/start?app=qtt"}
    )
    assert 'value="qtt" selected' in response.text


def test_flow_only_setup_does_not_require_bot_checks():
    steps = guide.onboarding(
        named=True,
        automations=0,
        flows=1,
        evidence=True,
        briefs=True,
        built=True,
        running=True,
        ready=False,
        declared=False,
        first_app="sample",
        has_baseline=True,
    )
    assert guide.next_step(steps) is None
    assert {s.id for s in steps if s.optional} == {"ready", "verdict"}
