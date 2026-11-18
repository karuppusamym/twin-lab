"""Readiness freshness: what a check was made against, whether it still holds, how the portal
says so; the short-lived lab reads behind the dashboard; and the app page keeping advanced
controls secondary. The lab agent is a stand-in."""

from __future__ import annotations

import os
import re
import time

import yaml
from fastapi.testclient import TestClient
from test_portal import repo, wait_job  # noqa: F401 - the fixture
from test_portal_round4 import FakeAgent, setup
from test_portal_round5 import LeaseAgent

from labctl import conformance as conf
from labctl import copies as copies_mod
from labctl import lab as lab_mod
from labctl import repo as repo_mod
from labctl.portal import guide
from labctl.portal.app import create_app
from labctl.portal.labreads import LabReads, Reading

AUTO = "uipath-case-update"
BASELINE = {"id": "bl_20261116T110000Z_aaaaaa", "loaded_at": None}  # as the stand-in agent says it


OTHER = "agentic-case-update"  # also runs against showcase


def automation(root, auto=AUTO):
    model, _ = repo_mod.load(root / "automations" / auto / "automation.yaml", "automation")
    return model


def prepared(repo, monkeypatch, agent_class=FakeAgent):  # noqa: F811
    """The showcase app, built, with a brief, its lab running; returns (fake agent, client)."""
    setup(repo, monkeypatch)
    fake = agent_class()
    monkeypatch.setattr(lab_mod, "agent", lambda root, app_id, host=None: fake)
    monkeypatch.setattr(conf, "image_id", lambda app_id: "img-1")
    (repo / "apps" / "showcase" / "BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    return fake, TestClient(create_app(repo))


def check_result(repo, verdict="READY", with_fingerprint=True, auto=AUTO):  # noqa: F811
    result = {"automation": auto, "verdict": verdict, "counts": {}, "checked_basis": {},
              "errors": [], "items": []}  # fmt: skip
    if with_fingerprint:
        result["fingerprint"] = conf.fingerprint(repo, automation(repo, auto))
    return result


def save(client, result):
    auto = result["automation"]
    job_id = client.app.state.jobs.submit("conformance", "Conformance", lambda log: result, auto)
    assert wait_job(client, f"/jobs/{job_id}")["status"] == "done"


def save_both(client, repo, **kw):  # noqa: F811
    """Both bots that run against showcase, checked now."""
    for auto in (AUTO, OTHER):
        save(client, check_result(repo, auto=auto, **kw))


def forget(client):
    client.app.state.reads.invalidate()


# --- what a check records ------------------------------------------------------------------------


def test_a_check_records_what_it_was_checked_against(repo, monkeypatch):  # noqa: F811
    fake, _ = prepared(repo, monkeypatch)
    report = conf.check(repo, automation(repo), host="127.0.0.1", reset=False)
    fp = report.fingerprint
    assert fp["version"] == 1 and fp["checked_at"].endswith("+00:00")
    assert fp["automation"]["id"] == AUTO and len(fp["automation"]["digest"]) == 12
    app = fp["apps"]["showcase"]
    assert app["brief"] and app["app_yaml"] and app["changed_at"] > 0
    assert app["database"] == "postgres" and app["image"] == "img-1"
    assert app["baseline"] == BASELINE and app["baseline_read"] is True
    assert report.to_dict()["fingerprint"] == fp  # the --json output and the portal's saved result
    line = [x for x in conf.render(report).splitlines() if "Checked against:" in x]
    assert len(line) == 1 and "baseline bl_20261116T110000Z_aaaaaa" in line[0]
    assert (
        "database postgres" in line[0] and "image img-1" in line[0] and "automation.yaml" in line[0]
    )


def test_a_check_without_a_lab_says_the_baseline_was_not_read(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)

    def nothing(root, app_id, host=None):
        raise lab_mod.LabError("not running")

    monkeypatch.setattr(lab_mod, "agent", nothing)
    fp = conf.fingerprint(repo, automation(repo))
    assert fp["apps"]["showcase"]["baseline_read"] is False
    assert "baseline unknown" in conf.describe(fp)


# --- each reason --------------------------------------------------------------------------------


def judged(repo, result, baselines=None, images=None):  # noqa: F811
    base = {"showcase": BASELINE} if baselines is None else baselines
    return conf.assess(repo, result, automation(repo), base, images=images)


def test_current_when_nothing_changed(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    f = judged(repo, check_result(repo))
    assert f.state == "current" and f.ready and not f.needs_recheck
    assert re.fullmatch(r"READY, checked \d{4}-\d\d-\d\d \d\d:\d\d UTC, still current", f.text)


def test_a_blocked_result_is_never_ready(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    f = judged(repo, check_result(repo, "BLOCKED"))
    assert f.state == "current" and not f.ready and not f.needs_recheck
    assert f.text.startswith("BLOCKED, checked")


def test_the_automation_changed(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    result = check_result(repo)
    path = repo / "automations" / AUTO / "automation.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    f = judged(repo, result)
    assert f.state == "stale" and not f.ready and f.needs_recheck
    assert f.reasons == ["the automation changed"]
    assert f.text.startswith("READY, but out of date: the automation changed since ")
    assert f.text.endswith(" — check again")


def test_the_brief_changed(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    result = check_result(repo)
    (repo / "apps" / "showcase" / "BRIEF.md").write_text("# A new brief\n", encoding="utf-8")
    f = judged(repo, result)
    assert f.state == "stale" and f.reasons == ["the app's brief changed"]  # not also "rebuilt"


def test_the_schema_changed(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    schema = repo / "apps" / "showcase" / "db" / "schema.sql"
    schema.parent.mkdir()
    schema.write_text("create table a (id int);\n", encoding="utf-8")
    result = check_result(repo)
    schema.write_text("create table a (id int, b int);\n", encoding="utf-8")
    assert judged(repo, result).reasons == ["the app's schema changed"]


def test_the_app_was_rebuilt_by_its_files(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    result = check_result(repo)
    dockerfile = repo / "apps" / "showcase" / "Dockerfile"
    later = time.time() + 120
    os.utime(dockerfile, (later, later))
    f = judged(repo, result)
    assert f.state == "stale" and f.reasons == ["the app was rebuilt"]
    # a change within the 2 second tolerance is not a rebuild
    soon = result["fingerprint"]["apps"]["showcase"]["changed_at"] + 1
    os.utime(dockerfile, (soon, soon))
    assert judged(repo, result).state == "current"


def test_the_app_was_rebuilt_by_its_image(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    result = check_result(repo)
    assert judged(repo, result, images={"showcase": "img-1"}).state == "current"
    assert judged(repo, result, images={"showcase": "img-2"}).reasons == ["the app was rebuilt"]
    assert judged(repo, result, images={}).state == "current"  # unknown now: not compared


def test_the_database_choice_changed(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    result = check_result(repo)
    path = repo / "apps" / "showcase" / "app.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["lab"]["database"] = "sqlite"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    f = judged(repo, result)
    assert f.state == "stale" and f.reasons == ["the database choice of showcase changed"]


def test_ports_alone_do_not_make_a_check_stale(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    result = check_result(repo)
    path = repo / "apps" / "showcase" / "app.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["lab"]["port"] = 8999
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert judged(repo, result).state == "current"


def test_the_data_baseline_changed(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    result = check_result(repo)
    f = judged(repo, result, {"showcase": {"id": "bl_20261117T090000Z_cccccc", "loaded_at": "x"}})
    assert f.state == "stale" and f.reasons == ["the data baseline changed"]
    assert judged(repo, result, {"showcase": None}).reasons == ["the data baseline changed"]


def test_several_reasons_are_all_named(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    result = check_result(repo)
    path = repo / "automations" / AUTO / "automation.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    other = {"showcase": {"id": "bl_other", "loaded_at": "x"}}
    f = judged(repo, result, other)
    assert "the automation changed and the data baseline changed since" in f.text


def test_an_unreadable_lab_cannot_confirm_freshness(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    result = check_result(repo)
    f = judged(repo, result, {})  # the lab did not answer
    assert f.state == "unconfirmed" and not f.ready and f.needs_recheck
    assert "cannot be confirmed" in f.text and "stopped or not reachable" in f.text
    assert f.text.startswith("READY, checked ")
    # what can be seen without the lab still counts: a changed brief is certain
    (repo / "apps" / "showcase" / "BRIEF.md").write_text("# New\n", encoding="utf-8")
    assert judged(repo, result, {}).state == "stale"


def test_results_saved_before_tracking_and_never_checked(repo, monkeypatch):  # noqa: F811
    prepared(repo, monkeypatch)
    old = check_result(repo, with_fingerprint=False)
    f = judged(repo, old)
    assert f.state == "legacy" and not f.ready and f.needs_recheck
    assert f.text == "checked before freshness was tracked — check again"
    none = judged(repo, None)
    assert none.state == "none" and none.text == "not checked" and not none.needs_recheck


# --- guide ---------------------------------------------------------------------------------------


def steps_for(**kw):
    base = dict(has_evidence=True, has_brief=True, built=True, running=True, automations=["a"],
                ready=[], has_baseline=True, sessions=0, declared=[])  # fmt: skip
    return guide.app_steps("x", **{**base, **kw})


def test_an_out_of_date_ready_makes_the_next_step_a_recheck():
    steps = steps_for(recheck={"a": "the app was rebuilt"})
    nxt = guide.next_step(steps)
    assert nxt.id == "readiness" and nxt.state == "next"
    assert nxt.hint.startswith("Check readiness again: a (the app was rebuilt)")
    never = guide.next_step(steps_for())
    assert never.hint.startswith("Check readiness: does each bot")
    assert guide.next_step(steps_for(ready=["a"])).id == "sessions"


def test_onboarding_rechecks_too():
    def path(**kw):
        base = dict(named=True, automations=1, flows=0, evidence=True, briefs=True, built=True,
                    running=True, ready=False, declared=False, first_app="x", has_baseline=True)  # fmt: skip
        return guide.onboarding(**{**base, **kw})

    nxt = guide.next_step(path(recheck="a (the data baseline changed)"))
    assert nxt.id == "ready" and nxt.hint == "Check readiness again: a (the data baseline changed)."


# --- the portal ----------------------------------------------------------------------------------


def test_every_page_shows_a_current_ready_and_counts_it(repo, monkeypatch):  # noqa: F811
    fake, client = prepared(repo, monkeypatch)
    save_both(client, repo)
    for url in ("/mapping", "/automations", f"/automations/{AUTO}", "/apps/showcase"):
        text = client.get(url).text
        assert re.search(r"READY, checked [\d\- :]+ UTC, still current", text), url
    overview = client.get("/").text
    assert "2 / 2" in overview and "out of date" not in overview
    assert 'title="Readiness: done"' in overview
    start = client.get("/start?app=showcase").text
    assert "READY, checked" in start and "still current" in start


def test_a_changed_baseline_is_shown_and_is_not_counted(repo, monkeypatch):  # noqa: F811
    fake, client = prepared(repo, monkeypatch)
    save_both(client, repo)
    assert "2 / 2" in client.get("/").text
    fake.info = lambda: {
        "baseline": {"id": "bl_new", "label": "reloaded"},
        "tables": [],
        "session": None,
    }
    assert "2 / 2" in client.get("/").text  # still inside the few seconds the read is kept
    forget(client)
    overview = client.get("/").text
    assert "0 / 2" in overview and "2 out of date: check again" in overview
    assert 'title="Readiness: next"' in overview
    for url in ("/mapping", "/automations", f"/automations/{AUTO}"):
        text = client.get(url).text
        assert "READY, but out of date: the data baseline changed since" in text, url
        assert "— check again" in text
    page = client.get("/apps/showcase").text
    assert (
        "Check readiness again: " in page
        and "uipath-case-update (the data baseline changed)" in page
    )
    assert 'id="guided-next"' in page
    assert "Check readiness again</button>" in client.get(f"/automations/{AUTO}").text
    start = client.get("/start?app=showcase").text
    assert "Check readiness again: " in start and "uipath-case-update (the data baseline" in start


def test_a_rebuilt_app_and_a_changed_automation_are_named(repo, monkeypatch):  # noqa: F811
    _, client = prepared(repo, monkeypatch)
    save_both(client, repo)
    later = time.time() + 120
    os.utime(repo / "apps" / "showcase" / "Dockerfile", (later, later))
    forget(client)
    assert "READY, but out of date: the app was rebuilt since" in client.get("/mapping").text
    assert "0 / 2" in client.get("/").text
    path = repo / "automations" / AUTO / "automation.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    text = client.get("/automations").text
    assert "the app was rebuilt and the automation changed since" in text or (
        "the automation changed and the app was rebuilt since" in text
    )


def test_an_unreachable_lab_is_said_not_guessed(repo, monkeypatch):  # noqa: F811
    fake, client = prepared(repo, monkeypatch)
    save_both(client, repo)

    def down():
        raise lab_mod.LabError("lab agent is not reachable")

    fake.info = down
    forget(client)
    overview = client.get("/").text
    assert "lab not reachable" in overview and "0 / 2" in overview
    text = client.get("/mapping").text
    assert "cannot be confirmed" in text and "stopped or not reachable" in text
    assert ", still current" not in text
    assert "lab not reachable" in client.get("/apps/showcase").text


def test_old_results_and_unchecked_bots(repo, monkeypatch):  # noqa: F811
    _, client = prepared(repo, monkeypatch)
    assert "not checked" in client.get("/mapping").text
    save(client, check_result(repo, with_fingerprint=False))
    for url in ("/mapping", "/automations", f"/automations/{AUTO}"):
        assert "checked before freshness was tracked — check again" in client.get(url).text, url
    assert "0 / 2" in client.get("/").text


def test_the_portal_check_job_saves_the_fingerprint(repo, monkeypatch):  # noqa: F811
    _, client = prepared(repo, monkeypatch)
    monkeypatch.setattr(conf, "_check_screens", lambda *a, **k: None)
    monkeypatch.setattr(conf, "_check_routes", lambda *a, **k: None)
    monkeypatch.setattr(conf, "_check_tables", lambda *a, **k: None)
    monkeypatch.setattr(conf, "_check_fidelity", lambda *a, **k: None)
    monkeypatch.setattr(conf, "_reset", lambda *a, **k: None)
    r = client.post(f"/automations/{AUTO}/conformance", follow_redirects=False)
    job = wait_job(client, r.headers["location"])
    assert job["status"] == "done", job["log"]
    assert job["result"]["fingerprint"]["apps"]["showcase"]["baseline"]["id"] == BASELINE["id"]
    assert "Checked against:" in job["log"]


# --- short-lived, bounded reads ------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_a_read_is_kept_for_a_few_seconds_then_read_again():
    clock, calls = Clock(), []
    reads = LabReads(ttl=5, clock=clock)

    def load(app_id):
        calls.append(app_id)
        return Reading(True, {"n": len(calls)}, [])

    assert reads.get("a", load).info == {"n": 1}
    assert reads.get("a", load).info == {"n": 1} and calls == ["a"]  # a hit
    clock.now += 4.9
    reads.get("a", load)
    assert calls == ["a"]
    clock.now += 0.2  # past the lifetime
    assert reads.get("a", load).info == {"n": 2}
    assert reads.get("b", load).info == {"n": 3}  # per app


def test_an_action_throws_the_kept_read_away():
    reads, calls = LabReads(ttl=60), []

    def load(app_id):
        calls.append(app_id)
        return Reading(True, {}, [])

    reads.get("a", load)
    reads.get("b", load)
    reads.invalidate("a")
    reads.get("a", load)
    reads.get("b", load)
    assert calls == ["a", "b", "a"]
    reads.invalidate()
    reads.get("b", load)
    assert calls == ["a", "b", "a", "b"]


def test_a_read_that_began_before_a_change_is_not_kept():
    reads, release, calls = LabReads(ttl=60), __import__("threading").Event(), []

    def load(app_id):
        calls.append(app_id)
        release.wait(2)
        return Reading(True, {"old": True}, [])

    future = reads.pool.submit(reads.get, "a", load)
    time.sleep(0.1)
    reads.invalidate("a")
    release.set()
    assert future.result(3).info == {"old": True}  # the caller gets its answer ...
    reads.get("a", load)
    assert calls == ["a", "a"]  # ... but the next page reads afresh


def test_a_slow_lab_times_out_and_does_not_stall_the_others():
    reads = LabReads(ttl=60, wait=0.3)

    def load(app_id):
        if app_id == "slow":
            time.sleep(1.5)
        return Reading(True, {"app": app_id}, [])

    started = time.monotonic()
    got = reads.get_many(["fast1", "slow", "fast2"], load)
    assert time.monotonic() - started < 1.0
    assert got["fast1"].ok and got["fast2"].ok
    assert not got["slow"].ok and "did not answer in time" in got["slow"].error
    # asked again at once: the failure is kept, nobody waits on the stuck read again
    started = time.monotonic()
    assert not reads.get("slow", load).ok
    assert time.monotonic() - started < 0.2
    time.sleep(1.5)  # the stuck read finishes: the answer replaces the failure
    assert reads.get("slow", load).ok


def test_a_failing_read_is_not_reachable_and_apps_are_read_at_the_same_time():
    reads = LabReads(ttl=60, wait=5)

    def load(app_id):
        if app_id == "dead":
            raise lab_mod.LabError("lab agent is not reachable")
        time.sleep(0.4)
        return Reading(True, {}, [])

    started = time.monotonic()
    got = reads.get_many(["a", "b", "c", "dead"], load)
    assert time.monotonic() - started < 1.0  # three 0.4 s reads, one after another, would be 1.2 s
    assert [got[x].ok for x in ("a", "b", "c", "dead")] == [True, True, True, False]
    assert "not reachable" in got["dead"].error


def test_the_pool_is_bounded():
    reads = LabReads(ttl=60, wait=5, workers=2)
    active, peak, lock = [0], [0], __import__("threading").Lock()

    def load(app_id):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.1)
        with lock:
            active[0] -= 1
        return Reading(True, {}, [])

    reads.get_many([str(i) for i in range(6)], load)
    assert peak[0] <= 2


def test_the_dashboard_reads_each_lab_once_and_an_action_forgets(repo, monkeypatch):  # noqa: F811
    fake, client = prepared(repo, monkeypatch)
    fake.reset = lambda: {}
    calls = []
    original = fake.info
    fake.info = lambda: calls.append(1) or original()
    client.get("/")
    client.get("/")
    client.get("/start?app=showcase")
    assert len(calls) == 1  # three pages, one read
    client.post("/apps/showcase/data/reset", follow_redirects=False)  # an action changing the lab
    client.get("/")
    assert len(calls) == 2


def test_a_finished_lab_job_forgets_the_read(repo, monkeypatch):  # noqa: F811
    fake, client = prepared(repo, monkeypatch)
    client.get("/")
    reads = client.app.state.reads
    assert "showcase" in reads.kept
    job_id = client.app.state.jobs.submit("lab", "Start showcase", lambda log: {}, "showcase")
    wait_job(client, f"/jobs/{job_id}")
    deadline = time.time() + 3
    while ("showcase" in reads.kept or "showcase" not in reads.images) and time.time() < deadline:
        time.sleep(0.05)
    assert "showcase" not in reads.kept
    assert reads.images.get("showcase") == "img-1"  # the image seen after a start


def test_a_dead_lab_does_not_stall_the_dashboard(repo, monkeypatch):  # noqa: F811
    fake, client = prepared(repo, monkeypatch)
    client.app.state.reads.wait = 0.3
    fake.info = lambda: time.sleep(1.5) or {}
    started = time.monotonic()
    page = client.get("/")
    assert page.status_code == 200 and time.monotonic() - started < 1.2
    assert "lab not reachable" in page.text


# --- the app page: the guided path first, advanced controls secondary --------------------------------

PRIMARY = [
    'action="/apps/showcase/evidence"', 'id="ekind"', 'action="/apps/showcase/brief"',
    'action="/apps/showcase/lab/up"', 'action="/apps/showcase/lab/down"',
    'action="/apps/showcase/data/generate"', 'id="seed"', 'id="counts"',
    'action="/apps/showcase/data/reset"', 'action="/apps/showcase/condition"',
    'action="/apps/showcase/session/start"', 'id="sauto"', 'action="/apps/showcase/devin/manual"',
]  # fmt: skip
SECONDARY = [
    'action="/apps/showcase/record"', 'action="/apps/showcase/record/stop"',
    'action="/apps/showcase/lease/take"', 'id="lmin"', 'action="/apps/showcase/copies"', 'id="cdata"',
    'action="/apps/showcase/database"', 'id="dbname"', 'action="/apps/showcase/lab-settings"',
    'id="lport"', 'action="/apps/showcase/data/upload"', 'action="/apps/showcase/data/baseline"',
    'action="/apps/showcase/packs/save"',
]  # fmt: skip


def inside_more(page):
    """For each control in PRIMARY and SECONDARY: is it inside a `details.more` group? Also the
    ids of the groups. (Read with an HTML parser, so nested groups and forms are followed.)"""
    from html.parser import HTMLParser

    wanted = {n: tuple(n.replace('"', "").split("=", 1)) for n in PRIMARY + SECONDARY}
    out = {"ids": []}
    void = {"input", "br", "hr", "meta", "link", "img"}

    def is_more(tag, a):
        return tag == "details" and "more" in (a.get("class") or "").split()

    class Walk(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if is_more(tag, a):
                out["ids"].append(a.get("id"))
            for needle, (key, value) in wanted.items():
                if a.get(key) == value:
                    out[needle] = any(more for _, more in self.stack)
            if tag not in void:
                self.stack.append((tag, is_more(tag, a)))

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break

    Walk().feed(page)
    return out


def test_primary_controls_sit_outside_the_more_groups_and_secondary_ones_inside(repo, monkeypatch):  # noqa: F811
    _, client = prepared(repo, monkeypatch)
    page = client.get("/apps/showcase").text
    for needle in PRIMARY + SECONDARY:
        assert needle in page, needle  # nothing was removed, ids and actions are unchanged
    inside = inside_more(page)
    assert {"more-evidence", "more-build", "more-lab", "more-data"} <= set(inside["ids"])
    for needle in PRIMARY:
        assert not inside[needle], f"{needle} should be outside the More groups"
    for needle in SECONDARY:
        assert inside[needle], f"{needle} should be inside a More group"
    for name in ("more-evidence", "more-build", "more-lab", "more-data"):  # collapsed by default
        assert re.search(rf'<details class="more" id="{name}"\s*>', page), name


def test_a_group_opens_by_itself_only_for_active_state(repo, monkeypatch):  # noqa: F811
    fake, client = prepared(repo, monkeypatch, LeaseAgent)
    fake.lease_now = {"holder": "Priya", "until": "2026-11-17T18:00:00.000Z", "purpose": "x"}
    page = client.get("/apps/showcase").text
    assert re.search(r'<details class="more" id="more-lab"\s+open', page)
    fake.lease_now = None
    forget(client)
    assert re.search(r'<details class="more" id="more-lab"\s*>', client.get("/apps/showcase").text)

    monkeypatch.setattr(copies_mod, "listing", lambda root, app_id=None: [
        {"lab": "showcase.run-1", "name": "run-1", "port": 8801, "db_port": 8802, "data": "baseline"}])  # fmt: skip
    page = client.get("/apps/showcase").text
    assert re.search(r'<details class="more" id="more-lab"\s+open', page)
    assert re.search(r"<details open><summary>Copies for a run \(1 running\)", page)
    monkeypatch.setattr(copies_mod, "listing", lambda root, app_id=None: [])

    path = repo / "apps" / "showcase" / "app.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["lab"]["database"] = "sqlite"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    page = client.get("/apps/showcase").text
    assert re.search(r'<details class="more" id="more-lab"\s+open', page)
    assert re.search(r"<details open><summary>Database: ", page)


def test_the_guided_line_stays_until_a_completed_session_on_a_ready_bot(repo, monkeypatch):  # noqa: F811
    fake, client = prepared(repo, monkeypatch)
    page = client.get("/apps/showcase").text
    assert 'id="guided-next"' in page and "Next step:" in page  # not checked yet
    assert 'href="/mapping"' in page.split('id="guided-next"')[1].split("</p>")[0]
    save_both(client, repo)
    forget(client)
    page = client.get("/apps/showcase").text
    assert 'id="guided-next"' in page  # READY, but no completed session
    assert "Sessions" in page.split('id="guided-next"')[1].split("</p>")[0]
    done = {"session_id": "s1", "automation": AUTO, "ended": "2026-11-17T10:00:00Z", "summary": {}}
    fake.sessions = lambda: [done]
    forget(client)
    page = client.get("/apps/showcase").text
    assert 'id="guided-next"' not in page and 'class="card nextcard"' in page
    # the session counts only while the READY beside it is current
    fake.info = lambda: {"baseline": {"id": "bl_other"}, "tables": [], "session": None}
    forget(client)
    assert 'id="guided-next"' in client.get("/apps/showcase").text


def test_the_start_page_leads_with_the_steps_and_keeps_words_secondary(repo, monkeypatch):  # noqa: F811
    _, client = prepared(repo, monkeypatch)
    page = client.get("/start").text
    assert page.index("Your next step") < page.index("Your progress") < page.index('class="flow"')
    assert re.search(r'<details class="card more" id="glossary"\s*>', page)
    assert "Words used here" in page and "Verdict" in page
